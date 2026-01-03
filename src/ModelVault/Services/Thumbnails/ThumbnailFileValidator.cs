using System;
using System.IO;

namespace ModelVault.Services.Thumbnails;

internal static class ThumbnailFileValidator
{
    public static bool TryValidate(string path, out string reason)
    {
        reason = string.Empty;
        if (string.IsNullOrWhiteSpace(path))
        {
            reason = "MissingPath";
            return false;
        }

        if (!File.Exists(path))
        {
            reason = "FileMissing";
            return false;
        }

        try
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            if (!TryReadSignature(stream, out var signature))
            {
                reason = "UnreadableHeader";
                return false;
            }

            if (signature == ImageSignature.Png)
            {
                if (TryGetPngDimensions(stream, out var width, out var height) && width > 0 && height > 0)
                {
                    return true;
                }

                reason = "InvalidPngHeader";
                return false;
            }

            if (signature == ImageSignature.Jpeg)
            {
                if (TryGetJpegDimensions(stream, out var width, out var height) && width > 0 && height > 0)
                {
                    return true;
                }

                reason = "InvalidJpegHeader";
                return false;
            }

            reason = "UnknownImageSignature";
            return false;
        }
        catch (IOException)
        {
            reason = "FileLoadFailed";
            return false;
        }
        catch (UnauthorizedAccessException)
        {
            reason = "FileLoadFailed";
            return false;
        }
    }

    private static bool TryReadSignature(Stream stream, out ImageSignature signature)
    {
        signature = ImageSignature.Unknown;
        Span<byte> header = stackalloc byte[8];
        var read = stream.Read(header);
        if (read < 2)
        {
            return false;
        }

        if (read >= 8 &&
            header[0] == 0x89 &&
            header[1] == 0x50 &&
            header[2] == 0x4E &&
            header[3] == 0x47 &&
            header[4] == 0x0D &&
            header[5] == 0x0A &&
            header[6] == 0x1A &&
            header[7] == 0x0A)
        {
            signature = ImageSignature.Png;
            return true;
        }

        if (header[0] == 0xFF && header[1] == 0xD8)
        {
            signature = ImageSignature.Jpeg;
            return true;
        }

        return true;
    }

    private static bool TryGetPngDimensions(Stream stream, out int width, out int height)
    {
        width = 0;
        height = 0;
        if (!stream.CanSeek)
        {
            return false;
        }

        stream.Seek(0, SeekOrigin.Begin);
        Span<byte> buffer = stackalloc byte[24];
        var read = stream.Read(buffer);
        if (read < 24)
        {
            return false;
        }

        if (buffer[12] != (byte)'I' ||
            buffer[13] != (byte)'H' ||
            buffer[14] != (byte)'D' ||
            buffer[15] != (byte)'R')
        {
            return false;
        }

        width = ReadBigEndianInt32(buffer[16..20]);
        height = ReadBigEndianInt32(buffer[20..24]);
        return true;
    }

    private static bool TryGetJpegDimensions(Stream stream, out int width, out int height)
    {
        width = 0;
        height = 0;
        if (!stream.CanSeek)
        {
            return false;
        }

        stream.Seek(2, SeekOrigin.Begin); // Skip SOI
        while (stream.Position < stream.Length)
        {
            if (!TryReadMarker(stream, out var marker))
            {
                return false;
            }

            if (marker == 0xD9 || marker == 0xDA)
            {
                return false;
            }

            if (!TryReadBigEndianUInt16(stream, out var length))
            {
                return false;
            }

            if (length < 2)
            {
                return false;
            }

            if (marker is 0xC0 or 0xC1 or 0xC2 or 0xC3 or 0xC5 or 0xC6 or 0xC7 or 0xC9 or 0xCA or 0xCB or 0xCD or 0xCE or 0xCF)
            {
                if (stream.ReadByte() == -1)
                {
                    return false;
                }

                if (!TryReadBigEndianUInt16(stream, out var h) ||
                    !TryReadBigEndianUInt16(stream, out var w))
                {
                    return false;
                }

                width = w;
                height = h;
                return true;
            }

            stream.Seek(length - 2, SeekOrigin.Current);
        }

        return false;
    }

    private static bool TryReadMarker(Stream stream, out byte marker)
    {
        marker = 0;
        int value;
        do
        {
            value = stream.ReadByte();
            if (value == -1)
            {
                return false;
            }
        } while (value != 0xFF);

        do
        {
            value = stream.ReadByte();
            if (value == -1)
            {
                return false;
            }
        } while (value == 0xFF);

        marker = (byte)value;
        return true;
    }

    private static bool TryReadBigEndianUInt16(Stream stream, out int value)
    {
        value = 0;
        var hi = stream.ReadByte();
        var lo = stream.ReadByte();
        if (hi == -1 || lo == -1)
        {
            return false;
        }

        value = (hi << 8) | lo;
        return true;
    }

    private static int ReadBigEndianInt32(ReadOnlySpan<byte> bytes)
    {
        return (bytes[0] << 24) | (bytes[1] << 16) | (bytes[2] << 8) | bytes[3];
    }

    private enum ImageSignature
    {
        Unknown,
        Png,
        Jpeg
    }
}

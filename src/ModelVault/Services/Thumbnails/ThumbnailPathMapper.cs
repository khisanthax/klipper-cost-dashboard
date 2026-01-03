using System;
using System.IO;
using ModelVault.Services;

namespace ModelVault.Services.Thumbnails;

public interface IThumbnailPathMapper
{
    string? ToDatabasePath(string? filePath);
    string? ToAbsolutePath(string? dbPath);
}

public sealed class ThumbnailPathMapper : IThumbnailPathMapper
{
    private readonly IAppPaths _appPaths;
    private readonly string _cacheRoot;
    private readonly string _cacheRootWithSeparator;

    public ThumbnailPathMapper(IAppPaths appPaths)
    {
        _appPaths = appPaths;
        _cacheRoot = Path.GetFullPath(appPaths.CacheDirectory);
        _cacheRootWithSeparator = _cacheRoot.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                                 + Path.DirectorySeparatorChar;
    }

    public string? ToDatabasePath(string? filePath)
    {
        if (string.IsNullOrWhiteSpace(filePath))
        {
            return null;
        }

        if (!Path.IsPathRooted(filePath))
        {
            return filePath;
        }

        try
        {
            var fullPath = Path.GetFullPath(filePath);
            if (IsUnderCacheRoot(fullPath))
            {
                return Path.GetRelativePath(_cacheRoot, fullPath);
            }
        }
        catch (Exception)
        {
            // fall through
        }

        return filePath;
    }

    public string? ToAbsolutePath(string? dbPath)
    {
        if (string.IsNullOrWhiteSpace(dbPath))
        {
            return null;
        }

        if (Path.IsPathRooted(dbPath))
        {
            return dbPath;
        }

        return Path.Combine(_appPaths.CacheDirectory, dbPath);
    }

    private bool IsUnderCacheRoot(string fullPath)
    {
        return fullPath.StartsWith(_cacheRootWithSeparator, StringComparison.OrdinalIgnoreCase)
               || string.Equals(fullPath, _cacheRoot, StringComparison.OrdinalIgnoreCase);
    }
}

using System.Collections.Generic;

namespace ModelVault.Services.Thumbnails;

public static class ThumbnailPresets
{
    public const string Grid256 = "grid_256";

    public static IReadOnlyList<string> RequiredForUi { get; } = new[]
    {
        Grid256
    };

    public static string DefaultPreset => Grid256;
}

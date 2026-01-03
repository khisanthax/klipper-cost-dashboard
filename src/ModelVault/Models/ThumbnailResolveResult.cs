namespace ModelVault.Models;

public enum ThumbnailResolveReason
{
    Resolved,
    NoDbRow,
    NoDbPath,
    FileMissing,
    FileLoadFailed
}

public sealed class ThumbnailResolveResult
{
    public ThumbnailResolveResult(
        int modelId,
        string requestedPreset,
        string? resolvedPreset,
        string? resolvedPath,
        string? status,
        string? errorMessage,
        ThumbnailResolveReason reason,
        string? detail = null)
    {
        ModelId = modelId;
        RequestedPreset = requestedPreset;
        ResolvedPreset = resolvedPreset;
        ResolvedPath = resolvedPath;
        Status = status;
        ErrorMessage = errorMessage;
        Reason = reason;
        Detail = detail;
    }

    public int ModelId { get; }
    public string RequestedPreset { get; }
    public string? ResolvedPreset { get; }
    public string? ResolvedPath { get; }
    public string? Status { get; }
    public string? ErrorMessage { get; }
    public ThumbnailResolveReason Reason { get; }
    public string? Detail { get; }

    public bool IsResolved => Reason == ThumbnailResolveReason.Resolved && !string.IsNullOrWhiteSpace(ResolvedPath);
}

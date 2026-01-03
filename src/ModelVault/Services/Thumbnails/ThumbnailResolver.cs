using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Dapper;
using ModelVault.Models;
using ModelVault.Services.Database;

namespace ModelVault.Services.Thumbnails;

public interface IThumbnailResolver
{
    Task<ThumbnailResolveResult> GetBestThumbnailAsync(int modelId, string? requestedPreset = null);
    Task<IReadOnlyList<ThumbnailResolveResult>> GetBestThumbnailsAsync(IReadOnlyList<int> modelIds, string? requestedPreset = null);
}

public sealed class ThumbnailResolver : IThumbnailResolver
{
    private const string PreferredStatus = "done";
    private const int BatchSize = 300;
    private readonly IDbConnectionFactory _connectionFactory;
    private readonly IThumbnailPathMapper _pathMapper;

    public ThumbnailResolver(IDbConnectionFactory connectionFactory, IThumbnailPathMapper pathMapper)
    {
        _connectionFactory = connectionFactory;
        _pathMapper = pathMapper;
    }

    public async Task<ThumbnailResolveResult> GetBestThumbnailAsync(int modelId, string? requestedPreset = null)
    {
        var presets = ResolvePresets(requestedPreset);
        ThumbnailResolveResult? lastResult = null;

        foreach (var preset in presets)
        {
            var row = await GetBestRowAsync(modelId, preset).ConfigureAwait(false);
            var result = ResolveFromRow(modelId, requestedPreset ?? "best-of", preset, row);
            LogResolve(result);

            if (result.IsResolved)
            {
                return result;
            }

            lastResult = result;

            if (requestedPreset != null)
            {
                return result;
            }
        }

        return lastResult ?? new ThumbnailResolveResult(
            modelId,
            requestedPreset ?? "best-of",
            null,
            null,
            null,
            null,
            ThumbnailResolveReason.NoDbRow);
    }

    public async Task<IReadOnlyList<ThumbnailResolveResult>> GetBestThumbnailsAsync(IReadOnlyList<int> modelIds, string? requestedPreset = null)
    {
        if (modelIds.Count == 0)
        {
            return Array.Empty<ThumbnailResolveResult>();
        }

        var results = new Dictionary<int, ThumbnailResolveResult>();
        var remaining = new HashSet<int>(modelIds);
        var presets = ResolvePresets(requestedPreset);

        foreach (var preset in presets)
        {
            if (remaining.Count == 0)
            {
                break;
            }

            var rows = await GetBestRowsAsync(remaining, preset).ConfigureAwait(false);
            foreach (var row in rows)
            {
                var result = ResolveFromRow(row.ModelId, requestedPreset ?? "best-of", preset, row);
                LogResolve(result);
                results[row.ModelId] = result;
                remaining.Remove(row.ModelId);
            }

            if (requestedPreset != null)
            {
                break;
            }
        }

        foreach (var modelId in remaining)
        {
            var result = new ThumbnailResolveResult(
                modelId,
                requestedPreset ?? "best-of",
                null,
                null,
                null,
                null,
                ThumbnailResolveReason.NoDbRow);
            LogResolve(result);
            results[modelId] = result;
        }

        return modelIds.Select(id => results[id]).ToList();
    }

    private static IReadOnlyList<string> ResolvePresets(string? requestedPreset)
    {
        if (!string.IsNullOrWhiteSpace(requestedPreset))
        {
            return new[] { requestedPreset };
        }

        return ThumbnailPresets.RequiredForUi;
    }

    private async Task<ThumbnailLookup?> GetBestRowAsync(int modelId, string preset)
    {
        await using var connection = _connectionFactory.CreateConnection();
        await connection.OpenAsync().ConfigureAwait(false);

        const string sql = @"SELECT file_path AS FilePath,
                status AS Status,
                error_message AS ErrorMessage,
                updated_utc AS UpdatedUtc
            FROM thumbnails
            WHERE model_id = @ModelId AND preset = @Preset
            ORDER BY CASE WHEN file_path IS NOT NULL THEN 0 ELSE 1 END,
                     CASE WHEN status = @PreferredStatus THEN 0 ELSE 1 END,
                     updated_utc DESC
            LIMIT 1;";

        return await connection.QueryFirstOrDefaultAsync<ThumbnailLookup>(sql, new
        {
            ModelId = modelId,
            Preset = preset,
            PreferredStatus
        }).ConfigureAwait(false);
    }

    private async Task<IReadOnlyList<ThumbnailLookup>> GetBestRowsAsync(IEnumerable<int> modelIds, string preset)
    {
        var list = modelIds.Distinct().ToList();
        var results = new List<ThumbnailLookup>();
        for (var i = 0; i < list.Count; i += BatchSize)
        {
            var batch = list.Skip(i).Take(BatchSize).ToList();
            await using var connection = _connectionFactory.CreateConnection();
            await connection.OpenAsync().ConfigureAwait(false);

            const string sql = @"SELECT model_id AS ModelId,
                    file_path AS FilePath,
                    status AS Status,
                    error_message AS ErrorMessage,
                    updated_utc AS UpdatedUtc
                FROM (
                    SELECT model_id,
                           file_path,
                           status,
                           error_message,
                           updated_utc,
                           ROW_NUMBER() OVER (
                               PARTITION BY model_id
                               ORDER BY CASE WHEN file_path IS NOT NULL THEN 0 ELSE 1 END,
                                        CASE WHEN status = @PreferredStatus THEN 0 ELSE 1 END,
                                        updated_utc DESC
                           ) AS rn
                    FROM thumbnails
                    WHERE model_id IN @ModelIds AND preset = @Preset
                )
                WHERE rn = 1;";

            var rows = await connection.QueryAsync<ThumbnailLookup>(sql, new
            {
                ModelIds = batch,
                Preset = preset,
                PreferredStatus
            }).ConfigureAwait(false);

            results.AddRange(rows);
        }

        return results;
    }

    private ThumbnailResolveResult ResolveFromRow(int modelId, string requestedPreset, string preset, ThumbnailLookup? row)
    {
        if (row == null)
        {
            return new ThumbnailResolveResult(
                modelId,
                requestedPreset,
                preset,
                null,
                null,
                null,
                ThumbnailResolveReason.NoDbRow);
        }

        if (string.IsNullOrWhiteSpace(row.FilePath))
        {
            return new ThumbnailResolveResult(
                modelId,
                requestedPreset,
                preset,
                null,
                row.Status,
                row.ErrorMessage,
                ThumbnailResolveReason.NoDbPath);
        }

        var resolvedPath = _pathMapper.ToAbsolutePath(row.FilePath);
        if (string.IsNullOrWhiteSpace(resolvedPath))
        {
            return new ThumbnailResolveResult(
                modelId,
                requestedPreset,
                preset,
                null,
                row.Status,
                row.ErrorMessage,
                ThumbnailResolveReason.NoDbPath);
        }

        if (!File.Exists(resolvedPath))
        {
            return new ThumbnailResolveResult(
                modelId,
                requestedPreset,
                preset,
                resolvedPath,
                row.Status,
                row.ErrorMessage,
                ThumbnailResolveReason.FileMissing);
        }

        if (!ThumbnailFileValidator.TryValidate(resolvedPath, out var reason))
        {
            return new ThumbnailResolveResult(
                modelId,
                requestedPreset,
                preset,
                resolvedPath,
                row.Status,
                row.ErrorMessage,
                ThumbnailResolveReason.FileLoadFailed,
                reason);
        }

        return new ThumbnailResolveResult(
            modelId,
            requestedPreset,
            preset,
            resolvedPath,
            row.Status,
            row.ErrorMessage,
            ThumbnailResolveReason.Resolved);
    }

    private static void LogResolve(ThumbnailResolveResult result)
    {
        var reason = result.Reason.ToString();
        var path = result.ResolvedPath ?? "<null>";
        var status = result.Status ?? "<null>";
        var detail = string.IsNullOrWhiteSpace(result.Detail) ? string.Empty : $" detail={result.Detail}";
        Trace.WriteLine(
            $"[ThumbnailResolver] THUMB_RESOLVE modelId={result.ModelId}, requested={result.RequestedPreset}, " +
            $"preset={result.ResolvedPreset ?? "<null>"}, path={path}, status={status}, reason={reason}{detail}");
    }

    private sealed class ThumbnailLookup
    {
        public int ModelId { get; init; }
        public string? FilePath { get; init; }
        public string? Status { get; init; }
        public string? ErrorMessage { get; init; }
        public long UpdatedUtc { get; init; }
    }
}

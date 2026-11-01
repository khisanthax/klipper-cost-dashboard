(() => {
  const labels = {
    source_unavailable: 'Source file unavailable',
    no_slicer_thumbnail: 'No slicer thumbnail',
    network_error: 'Printer network error',
    source_auth_error: 'Printer authentication failed',
    source_changed: 'Source replaced since print; recovery skipped',
    insufficient_history: 'Insufficient history for safe recovery',
    missing_endpoint: 'Moonraker endpoint missing',
    archive_missing: 'Archived object missing; restore backup',
    invalid_source_path: 'Invalid source thumbnail path'
  };
  document.querySelectorAll('img.kcd-thumb').forEach(img => {
    let handled = false;
    async function unavailable() {
      if (handled) return;
      handled = true;
      let label = 'Thumbnail unavailable';
      try {
        const url = new URL(img.currentSrc || img.src, location.href);
        if (url.origin === location.origin) {
          const response = await fetch(url, {method: 'HEAD'});
          label = labels[response.headers.get('X-KCD-Thumbnail-Status')] || label;
        }
      } catch (_) { /* Keep a readable fallback when the Master is offline. */ }
      const note = document.createElement('span');
      note.className = 'kcd-thumb-note text-muted small';
      note.textContent = label;
      note.title = label;
      note.setAttribute('role', 'status');
      img.replaceWith(note);
    }
    img.addEventListener('error', unavailable);
    if (img.complete && img.naturalWidth === 0) unavailable();
    if (/\/(thumbnails|jobs)\//.test(img.getAttribute('src') || '')) {
      img.title = 'Permanent job thumbnail; source G-code is not required after archival';
    }
  });
})();

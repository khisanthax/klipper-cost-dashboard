(() => {
  const waiting=[];
  let active=0;
  function drain() {
    while(active<2 && waiting.length) {
      const img=waiting.shift();
      if(!img.isConnected){img.dataset.kcdQueued='';continue;}
      active++;
      let released=false;
      function done(){if(released)return;released=true;img.removeEventListener('load',done);img.removeEventListener('error',done);img.dataset.kcdQueued='';active--;drain();}
      img.addEventListener('load',done);img.addEventListener('error',done);
      img.loading='eager';
      img.src=img.dataset.kcdSrc;
    }
  }
  function queue(img){if(img.dataset.kcdQueued)return;img.dataset.kcdQueued='1';waiting.push(img);drain();}
  const observer='IntersectionObserver' in window ? new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){observer.unobserve(entry.target);queue(entry.target);}}),{rootMargin:'200px'}) : null;
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
          const status=response.headers.get('X-KCD-Thumbnail-Status');
          if(status==='recovery_deferred'&&img.dataset.kcdSrc){
            const retries=Number(img.dataset.kcdRetries||0);
            if(retries<3){img.dataset.kcdRetries=String(retries+1);handled=false;setTimeout(()=>{if(img.isConnected)queue(img);},2000);return;}
            label='Thumbnail recovery busy; reload to retry';
          } else label = labels[status] || (status==='archived'?'Archived image could not be displayed':label);
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
    if(img.dataset.kcdSrc){if(observer)observer.observe(img);else queue(img);}
    else if (img.complete && img.naturalWidth === 0) unavailable();
    if (/\/(thumbnails|jobs)\//.test(img.dataset.kcdSrc || img.getAttribute('src') || '')) {
      img.title = 'Permanent job thumbnail; source G-code is not required after archival';
    }
  });
})();

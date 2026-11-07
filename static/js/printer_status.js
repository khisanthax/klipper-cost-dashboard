/* Current observations are separate from retained KCD event state. */
(() => {
  let pending=false;
  function renderFilament(node, item, active) {
    node.replaceChildren();
    if (!active) { node.textContent=item.connected?'No active print':'Unverified'; return; }
    const value=item.filament_display;
    const primary=document.createElement('span');
    primary.dataset.filamentPrimary='';
    primary.dataset.filamentPreference=value?.preference||'';
    primary.textContent=value?.text||'Measurement unavailable';
    node.append(primary);
    const details=document.createElement('details');
    details.dataset.filamentDetails='';
    details.className='kcd-measurement-details small text-muted text-start mt-1';
    const summary=document.createElement('summary');
    summary.textContent='Measurement details';
    details.append(summary);
    const list=document.createElement('dl');
    for (const [label,text] of [
      ['Length', value?.meters!=null?value.meters+' m':'Unknown'],
      ['Estimated weight', value?.grams!=null?value.grams+' g':'Unknown'],
      ['Conversion factor', value?.factor!=null?value.factor+' g/m':'Unavailable'],
      ['Conversion source', value?.source||item.conversion_source||'No verified conversion']
    ]) {
      const term=document.createElement('dt'),definition=document.createElement('dd');
      term.textContent=label; definition.textContent=text;
      list.append(term,definition);
    }
    details.append(list); node.append(details);
  }
  window.kcdRefreshPrinterStatus=async () => {
    if(pending)return;
    pending=true;
    try {
      const response=await fetch('/api/printer-status',{cache:'no-store'});
      if(!response.ok)throw new Error('Status request failed');
      const {printers}=await response.json();
      document.querySelectorAll('[data-kcd-printer],[data-kcd-live-job]').forEach(node=>{
        const name=node.dataset.kcdPrinter||node.dataset.kcdLiveJob;
        const item=printers[name];if(!item)return;
        const badge=node.querySelector('[data-live-state]');
        if(badge){badge.textContent=item.state.toUpperCase();badge.className='badge '+({printing:'bg-success',paused:'bg-warning text-dark',error:'bg-danger',disconnected:'bg-danger',identity_mismatch:'bg-danger'}[item.state]||'bg-secondary');}
        const message=node.querySelector('[data-live-message]');
        if(message)message.textContent=item.tracking_warning||item.error||item.identity_warning||('Moonraker verified '+new Date(item.observed_at*1000).toLocaleTimeString());
        const active=item.connected&&['printing','paused'].includes(item.state)
          &&(!node.hasAttribute('data-kcd-require-identity')||item.identity_verified);
        const current=node.querySelector('[data-live-current]');
        if(current)current.textContent=active?item.filename+' · '+(item.filament_display?.text||'Measurement unavailable')+' · '+(item.total_cost!=null?'$'+item.total_cost.toFixed(2)+' estimated':item.pricing_error||'price unavailable'):'';
        const filename=node.querySelector('[data-live-filename]');
        if(filename&&active)filename.textContent=item.filename;
        const cost=node.querySelector('[data-live-cost]');
        if(cost){cost.textContent=active&&item.total_cost!=null?'$'+item.total_cost.toFixed(2):item.pricing_error||item.tracking_warning||item.error||(active?'Estimate unavailable':'No active print');cost.title='Live estimate includes material and time at current printer pricing; final billing applies the configured minimum hour.';}
        const filament=node.querySelector('[data-live-filament]');
        if(filament)renderFilament(filament,item,active);
        const elapsed=node.querySelector('[data-live-elapsed]');
        if(elapsed){elapsed.textContent=active?(Number.isFinite(item.print_seconds)?Math.floor(item.print_seconds/60)+' min active':'Elapsed time unavailable'):item.connected?'No active print':'Unverified';}
        const progress=node.querySelector('[data-live-progress]');
        if(progress) {
          const fraction=active&&Number.isFinite(item.progress)?item.progress:null;
          progress.hidden=fraction===null;
          progress.value=fraction===null?0:fraction;
          node.querySelector('[data-live-progress-label]').textContent=fraction===null?'Progress unavailable':(100*fraction).toFixed(0)+'%';
          const remaining=fraction>0&&fraction<1&&item.print_seconds>0
            ? Math.ceil(item.print_seconds*(1/fraction-1)/60) : null;
          node.querySelector('[data-live-remaining]').textContent=Number.isFinite(remaining)
            ? remaining+' min estimated from progress' : 'Estimate unavailable';
        }
        for(const [field,key] of [['time-cost','time_cost'],['material-cost','material_cost']]) {
          const component=node.querySelector('[data-live-'+field+']');
          if(component)component.textContent=active&&Number.isFinite(item[key])?'$'+item[key].toFixed(2)+' estimated':'Unavailable';
        }
        const detailFile=node.querySelector('[data-live-detail-file]');
        if(detailFile)detailFile.textContent=active?item.filename:'No verified active file';
      });
    } catch (_) {
      document.querySelectorAll('[data-live-message],[data-live-cost]').forEach(node=>node.textContent='Live status unavailable; displayed tracking may be stale');
      document.querySelectorAll('[data-live-current],[data-live-filament],[data-live-elapsed]').forEach(node=>node.textContent='Unverified');
      document.querySelectorAll('[data-live-state]').forEach(node=>{node.textContent='UNVERIFIED';node.className='badge bg-secondary';});
      document.querySelectorAll('[data-live-detail-file],[data-live-progress-label],[data-live-remaining],[data-live-time-cost],[data-live-material-cost]').forEach(node=>node.textContent='Unverified');
      document.querySelectorAll('[data-live-progress]').forEach(node=>node.hidden=true);
    } finally {pending=false;}
  };
})();

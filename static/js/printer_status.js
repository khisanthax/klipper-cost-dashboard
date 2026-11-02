/* Current observations are separate from retained KCD event state. */
(() => {
  let pending=false;
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
        if(badge){badge.textContent=item.state.toUpperCase();badge.className='badge '+({printing:'bg-success',paused:'bg-warning text-dark',error:'bg-danger',disconnected:'bg-danger'}[item.state]||'bg-secondary');}
        const message=node.querySelector('[data-live-message]');
        if(message)message.textContent=item.tracking_warning||item.error||('Moonraker verified '+new Date(item.observed_at*1000).toLocaleTimeString());
        const active=item.connected&&['printing','paused'].includes(item.state);
        const cost=node.querySelector('[data-live-cost]');
        if(cost){cost.textContent=active&&item.total_cost!=null?'$'+item.total_cost.toFixed(2):item.pricing_error||item.tracking_warning||item.error||'No active print';cost.title='Live estimate includes material and time at current printer pricing; final billing applies the configured minimum hour.';}
        const filament=node.querySelector('[data-live-filament]');
        if(filament){filament.textContent=active&&item.grams!=null?item.grams.toFixed(2)+' g ('+(item.filament_mm/1000).toFixed(2)+' m)':'—';filament.title=item.conversion_source||'No verified current weight';}
        const elapsed=node.querySelector('[data-live-elapsed]');
        if(elapsed){elapsed.textContent=active?Math.floor(item.print_seconds/60)+' min active':item.connected?'No active print':'Unverified';}
      });
    } catch (_) {
      document.querySelectorAll('[data-live-message],[data-live-cost]').forEach(node=>node.textContent='Live status unavailable; displayed tracking may be stale');
      document.querySelectorAll('[data-live-state]').forEach(node=>{node.textContent='UNVERIFIED';node.className='badge bg-secondary';});
    } finally {pending=false;}
  };
})();

/* RESOL single native HA dashboard. No protocol or business logic here. */
const COPY = {
  en: {overview:"Overview",solar:"Solar",heating:"Heating",hot_water:"Hot water",energy:"Energy & history",settings:"Settings",engineering:"Engineering",schedule:"Weekly schedules",refresh:"Refresh view",installation:"Installation",all:"All objects",unknown:"Unknown",unavailable:"Unavailable",empty:"No observed capabilities in this view.",error:"Request failed or uncertain. Check the current value.",loading:"Loading RESOL…",apply:"Apply",preview:"Review",cancel:"Cancel",add:"Add interval",remove:"Remove",history:"History",observed:"Observed and confirmed",submitted:"Submitted; check discovery progress",not_confirmed:"Outcome not confirmed",shared:"Shared program references",no_refs:"No references reported",configure:"Integration settings",configure_help:"Manage dashboard, Engineering access and gateway credentials in native Home Assistant settings.",search:"Search technical evidence",export:"Export redacted diagnostics",diagnostics:"Diagnostics",inventory:"Parameter inventory",none:"No captured parameters",close:"Close",time:"Last updated",no_history:"No recorded history",start:"Start",end:"End",confirm_schedule:"Review shared effects before saving. Each day is saved independently.",choose:"Choose an object",attention:"Needs attention",ready:"Observed",operation:"Operation",preferences:"Preferences",status:"Status",details:"Details",readonly:"Read only",back:"Back",enabled:"Enabled",select:"Select",on:"On",off:"Off",working:"Working…"},
  nl: {overview:"Overzicht",solar:"Zonne-energie",heating:"Verwarming",hot_water:"Warm water",energy:"Energie & historiek",settings:"Instellingen",engineering:"Engineering",schedule:"Weekprogramma’s",refresh:"Weergave vernieuwen",installation:"Installatie",all:"Alle objecten",unknown:"Onbekend",unavailable:"Niet beschikbaar",empty:"Geen waargenomen functies in deze weergave.",error:"Opdracht geweigerd of onzeker. Controleer de actuele waarde.",loading:"RESOL laden…",apply:"Toepassen",preview:"Controleren",cancel:"Annuleren",add:"Interval toevoegen",remove:"Verwijderen",history:"Historiek",observed:"Waargenomen en bevestigd",submitted:"Verstuurd; controleer de voortgang",not_confirmed:"Resultaat niet bevestigd",shared:"Gedeelde programmaverwijzingen",no_refs:"Geen verwijzingen gemeld",configure:"Integratie-instellingen",configure_help:"Beheer dashboard, Engineering en gatewaygegevens in de standaardinstellingen van Home Assistant.",search:"Technische informatie zoeken",export:"Geanonimiseerde diagnose exporteren",diagnostics:"Diagnose",inventory:"Parameterinventaris",none:"Geen parameters vastgelegd",close:"Sluiten",time:"Laatst bijgewerkt",no_history:"Geen geregistreerde historiek",start:"Begin",end:"Einde",confirm_schedule:"Controleer de gedeelde gevolgen voor je opslaat. Elke dag wordt afzonderlijk opgeslagen.",choose:"Kies een object",attention:"Aandacht nodig",ready:"Waargenomen",operation:"Bediening",preferences:"Voorkeuren",status:"Status",details:"Details",readonly:"Alleen lezen",back:"Terug",enabled:"Ingeschakeld",select:"Selecteren",on:"Aan",off:"Uit",working:"Bezig…"},
  fr: {overview:"Vue d’ensemble",solar:"Solaire",heating:"Chauffage",hot_water:"Eau chaude",energy:"Énergie et historique",settings:"Paramètres",engineering:"Ingénierie",schedule:"Programmes hebdomadaires",refresh:"Actualiser la vue",installation:"Installation",all:"Tous les objets",unknown:"Inconnu",unavailable:"Indisponible",empty:"Aucune fonction observée dans cette vue.",error:"Action refusée ou incertaine. Vérifiez la valeur actuelle.",loading:"Chargement de RESOL…",apply:"Appliquer",preview:"Vérifier",cancel:"Annuler",add:"Ajouter une plage",remove:"Supprimer",history:"Historique",observed:"Observé et confirmé",submitted:"Envoyé ; vérifiez la progression",not_confirmed:"Résultat non confirmé",shared:"Références du programme partagé",no_refs:"Aucune référence signalée",configure:"Paramètres de l’intégration",configure_help:"Gérez le tableau de bord, l’ingénierie et les accès dans les paramètres natifs de Home Assistant.",search:"Rechercher des données techniques",export:"Exporter le diagnostic expurgé",diagnostics:"Diagnostic",inventory:"Inventaire des paramètres",none:"Aucun paramètre capturé",close:"Fermer",time:"Dernière mise à jour",no_history:"Aucun historique enregistré",start:"Début",end:"Fin",confirm_schedule:"Vérifiez les effets partagés avant d’enregistrer. Chaque jour est enregistré séparément.",choose:"Choisir un objet",attention:"Attention requise",ready:"Observé",operation:"Commande",preferences:"Préférences",status:"État",details:"Détails",readonly:"Lecture seule",back:"Retour",enabled:"Activé",select:"Choisir",on:"Activé",off:"Désactivé",working:"En cours…"}
};
const SECTIONS = ["overview","solar","heating","hot_water","energy","settings","engineering"];
const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const style = " :host{display:block;color:var(--primary-text-color);background:var(--primary-background-color);min-height:100%;font:inherit}*{box-sizing:border-box}main{max-width:1440px;margin:auto;padding:clamp(12px,3vw,32px)}header{display:flex;gap:12px;align-items:center;flex-wrap:wrap}h1{font-size:1.8rem;margin:0}h2{font-size:1.1rem;margin:0 0 12px}h3{font-size:1rem;margin:0 0 6px}p{line-height:1.45}nav{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--divider-color);padding:12px 0;margin:10px 0 18px}button,input,select{font:inherit;color:inherit;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:8px;padding:9px;max-width:100%}button{cursor:pointer;min-height:40px}button:disabled{opacity:.5;cursor:not-allowed}button.active,button.primary{background:var(--primary-color);color:var(--text-primary-color,#fff);border-color:transparent}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:14px}.card{padding:16px;border:1px solid var(--divider-color);border-radius:14px;background:var(--card-background-color);min-width:0}.line{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:10px 0;border-bottom:1px solid var(--divider-color)}.line:last-child{border:0}.value{font-weight:600;overflow-wrap:anywhere}.muted,small{color:var(--secondary-text-color)}.warning{color:var(--warning-color,#aa6b00)}.actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.grow{flex:1}.toolbar{margin:12px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap}.technical{overflow:auto;max-height:65vh}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.85rem}.notice{padding:10px 0;min-height:1.8em}.interval{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:8px 0}.interval input{max-width:135px}table{width:100%;border-collapse:collapse;text-align:left;font-size:.85rem}th,td{padding:8px;border-bottom:1px solid var(--divider-color);overflow-wrap:anywhere}a{color:var(--primary-color)}@media(max-width:520px){main{padding:12px}nav button{font-size:.9rem;padding:8px}.card{padding:12px}.line{align-items:flex-start}.actions{width:100%}header select{width:100%}}";
class ResolDashboard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode:"open"});
    this.installations = [];
    this.tab = "overview";
    this.context = "";
    this.object = "";
    this.notice = "";
    this.loaded = false;
    this.loading = false;
    this.pending = false;
    this.report = null;
    this.query = "";
    this.editor = null;
    this.history = null;
    this.shadowRoot.addEventListener("click", event => this.click(event));
    this.shadowRoot.addEventListener("change", event => this.change(event));
    this.shadowRoot.addEventListener("input", event => {
      if(event.target.id==="technical-search"){this.query=event.target.value;this.renderInventory();}
    });
  }
  connectedCallback(){this.render();if(this._hass&&!this.loaded)this.load();}
  set hass(hass) {
    const previous=this._hass;
    this._hass=hass;
    if(!this.isConnected)return;
    if(!this.loaded&&!this.loading){this.load();return;}
    if(this.editor||this.pending||this.shadowRoot.activeElement)return;
    const rows=this.rows();
    if(previous?.language!==hass.language||rows.some(row=>previous?.states?.[row.entity_id]!==hass.states?.[row.entity_id])){
      this.render();
    }
  }
  t(key){return (COPY[(this._hass?.language||"en").slice(0,2)]||COPY.en)[key]||COPY.en[key]||key;}
  current(){return this.installations.find(item=>item.id===this.context);}
  rows(){return this.current()?.objects.flatMap(asset=>asset.capabilities)||[];}
  row(id){return this.rows().find(row=>row.entity_id===id);}
  async load(){
    if(!this._hass||this.loading)return;
    this.loading=true;
    try{
      this.installations=await this._hass.callWS({type:"deltasol/dashboard/list"});
      if(!this.current()){this.context=this.installations[0]?.id||"";this.object="";this.report=null;}
      if(!this.tabs().includes(this.tab))this.tab="overview";
      this.loaded=true;
    }catch{this.notice=this.t("error");this.loaded=true;}
    finally{this.loading=false;this.render();}
  }
  tabs(){
    const installation=this.current();
    const groups=new Set(this.rows().map(row=>row.section));
    return SECTIONS.filter(tab=>tab==="overview"||tab==="energy"||groups.has(tab)||tab==="settings"&&installation?.settings||tab==="engineering"&&installation?.engineering);
  }
  state(row){return this._hass?.states?.[row.entity_id];}
  value(row){
    const state=this.state(row);
    if(!state||state.state==="unknown"||state.state==="unavailable")return this.t(state?.state==="unavailable"?"unavailable":"unknown");
    const unit=state.attributes.unit_of_measurement;
    return state.state+(unit?" "+unit:"");
  }
  valid(row){const state=this.state(row);return Boolean(state&&state.state!=="unknown"&&state.state!=="unavailable");}
  allowed(row){return row.control&&this.valid(row)&&!this.pending;}
  control(row){
    const id=escape(row.entity_id), state=this.state(row), attrs=state?.attributes||{},disabled=this.allowed(row)?"":"disabled";
    if(!row.control)return "";
    if(row.schedule)return '<button data-edit="'+id+'" '+disabled+'>'+escape(this.t("preview"))+'</button>';
    if(row.kind==="switch")return '<button data-action="'+id+'" data-value="'+(state?.state!=="on")+'" '+disabled+'>'+escape(this.t(state?.state==="on"?"off":"on"))+'</button>';
    if(row.kind==="button")return '<button data-action="'+id+'" data-value="null" '+disabled+'>'+escape(this.t("apply"))+'</button>';
    if(row.kind==="select")return '<select aria-label="'+escape(row.name)+'" data-input="'+id+'" '+disabled+'>'+ (attrs.options||[]).map(v=>'<option '+(state.state===v?"selected":"")+'>'+escape(v)+'</option>').join("")+'</select><button data-apply="'+id+'" '+disabled+'>'+escape(this.t("apply"))+'</button>';
    if(row.kind==="number"||row.kind==="text"){
      const type=row.kind==="number"?"number":"text";
      const limits=type==="number"?["min","max","step"].map(k=>attrs[k]===undefined?"":" "+k+'="'+escape(attrs[k])+'"').join(""):"";
      return '<input aria-label="'+escape(row.name)+'" data-input="'+id+'" type="'+type+'" value="'+escape(state?.state||"")+'" '+limits+' '+disabled+'><button data-apply="'+id+'" '+disabled+'>'+escape(this.t("apply"))+'</button>';
    }
    return "";
  }
  detail(row,control){
    const state=this.state(row),updated=state?.last_updated?new Date(state.last_updated).toLocaleString(this._hass?.language):"";
    return '<div class="line"><div class="grow"><div>'+escape(row.name)+'</div><div class="value'+(this.valid(row)?"":" warning")+'">'+escape(this.value(row))+'</div>'+(updated?'<small title="'+escape(this.t("time"))+'">'+escape(updated)+'</small>':"")+'</div><div class="actions">'+(control?this.control(row):"")+'<button data-history="'+escape(row.entity_id)+'" aria-label="'+escape(this.t("history")+' '+row.name)+'">'+escape(this.t("history"))+'</button></div></div>';
  }
  visibleRows(asset){
    const rows=asset.capabilities;
    if(this.tab==="settings")return rows.filter(row=>row.depth==="settings"||row.depth==="controls");
    if(this.tab==="engineering")return rows.filter(row=>row.depth==="engineering");
    if(this.tab==="overview")return rows.filter(row=>row.depth==="status"||row.depth==="controls");
    return rows.filter(row=>row.section===this.tab&&(row.depth==="status"||row.depth==="controls"));
  }
  cards(){
    const assets=(this.current()?.objects||[]).filter(asset=>!this.object||asset.id===this.object);
    const cards=assets.map(asset=>{
      const rows=this.visibleRows(asset);
      if(!rows.length)return "";
      return '<article class="card"><div class="toolbar"><h2 class="grow">'+escape(asset.name)+'</h2>'+(this.object?"":'<button data-object="'+escape(asset.id)+'">'+escape(this.t("details"))+'</button>')+'</div>'+rows.map(row=>this.detail(row,row.depth!=="status")).join("")+'</article>';
    }).filter(Boolean);
    return cards.length?'<div class="grid">'+cards.join("")+'</div>':'<p class="muted">'+escape(this.t("empty"))+'</p>';
  }
  render(){
    if(!this.shadowRoot)return;
    const installation=this.current(), tabs=this.tabs(), assets=installation?.objects||[];
    const count=this.rows().filter(row=>row.depth==="status"&&!this.valid(row)).length;
    const options=this.installations.map(item=>'<option value="'+escape(item.id)+'" '+(item.id===this.context?"selected":"")+'>'+escape(item.name)+'</option>').join("");
    const objects=assets.map(asset=>'<option value="'+escape(asset.id)+'" '+(asset.id===this.object?"selected":"")+'>'+escape(asset.name)+'</option>').join("");
    this.shadowRoot.innerHTML='<style>'+style+'</style><main><header><ha-icon icon="mdi:solar-power"></ha-icon><h1 class="grow">RESOL</h1><select id="installation" aria-label="'+escape(this.t("installation"))+'">'+options+'</select><button data-refresh>'+escape(this.t("refresh"))+'</button></header><nav aria-label="RESOL">'+tabs.map(tab=>'<button data-tab="'+tab+'" class="'+(this.tab===tab?"active":"")+'" aria-current="'+(this.tab===tab?"page":"false")+'">'+escape(this.t(tab))+'</button>').join("")+'</nav><div role="status" class="notice">'+escape(this.notice)+'</div>'+(!this.loaded?'<p>'+escape(this.t("loading"))+'</p>':!installation?'<p>'+escape(this.t("empty"))+'</p>':'<div class="toolbar"><select id="object" aria-label="'+escape(this.t("choose"))+'"><option value="">'+escape(this.t("all"))+'</option>'+objects+'</select>'+(this.tab==="overview"?'<span class="'+(count?"warning":"muted")+'">'+escape(count?this.t("attention")+": "+count:this.t("ready"))+'</span>':"")+'</div>'+(this.tab==="settings"?'<section class="card"><h2>'+escape(this.t("configure"))+'</h2><p>'+escape(this.t("configure_help"))+'</p><a href="/config/integrations/integration/deltasol">'+escape(this.t("configure"))+'</a></section>':"")+(this.tab==="engineering"?this.engineeringView():"")+this.cards())+(this.editor?this.editorView():"")+(this.history?this.historyView():"")+'</main>';
    if(this.tab==="engineering")this.renderInventory();
  }
  async engineering(){
    this.report=null;
    this.render();
    const id=this.context;
    try{
      const report=await this._hass.callWS({type:"deltasol/dashboard/engineering",entry_id:id});
      if(this.context===id&&this.tab==="engineering")this.report=report;
    }catch{this.notice=this.t("error");}
    this.render();
  }
  engineeringView(){
    if(!this.report)return '<p>'+escape(this.t("loading"))+'</p>';
    const summary={...this.report};
    if(summary.configuration_discovery){summary.configuration_discovery={...summary.configuration_discovery};delete summary.configuration_discovery.parameters;}
    return '<section class="card"><h2>'+escape(this.t("diagnostics"))+'</h2><button data-export>'+escape(this.t("export"))+'</button><details><summary>'+escape(this.t("details"))+'</summary><pre>'+escape(JSON.stringify(summary,null,2))+'</pre></details><h2>'+escape(this.t("inventory"))+'</h2><input id="technical-search" aria-label="'+escape(this.t("search"))+'" placeholder="'+escape(this.t("search"))+'" value="'+escape(this.query)+'"><div class="technical" id="inventory"></div></section>';
  }
  renderInventory(){
    const host=this.shadowRoot.querySelector("#inventory");
    if(!host)return;
    const entries=Object.entries(this.report?.configuration_discovery?.parameters||{}).filter(([key,row])=>(key+" "+JSON.stringify(row)).toLowerCase().includes(this.query.toLowerCase()));
    host.innerHTML=entries.length?'<table><thead><tr><th>Parameter</th><th>'+escape(this.t("status"))+'</th><th>'+escape(this.t("details"))+'</th></tr></thead><tbody>'+entries.map(([key,row])=>'<tr><td>'+escape(key)+'</td><td>'+escape(row.write_access||row.status||"")+'</td><td><details><summary>'+escape(this.t("details"))+'</summary><pre>'+escape(JSON.stringify(row,null,2))+'</pre></details></td></tr>').join("")+'</tbody></table>':'<p>'+escape(this.t("none"))+'</p>';
  }
  edit(row){
    const observed=this.state(row)?.state;
    if(!this.allowed(row)||!row.schedule||!this.valid(row))return;
    this.editor={id:row.entity_id,windows:observed?observed.split(",").map(interval=>interval.trim().split("-")):[],review:null};
    this.render();
  }
  editorView(){
    const editor=this.editor,row=this.row(editor.id);
    if(!row)return "";
    return '<section class="card"><h2>'+escape(row.name)+'</h2><p>'+escape(this.t("confirm_schedule"))+'</p>'+editor.windows.map((pair,i)=>'<div class="interval"><label>'+escape(this.t("start"))+' <input type="time" step="900" data-window="'+i+'" data-edge="0" value="'+escape(pair[0])+'"></label><label>'+escape(this.t("end"))+' <input type="time" step="900" data-window="'+i+'" data-edge="1" value="'+escape(pair[1])+'"></label><button data-remove="'+i+'">'+escape(this.t("remove"))+'</button></div>').join("")+'<p>'+escape(this.t("shared"))+': '+escape((editor.review?.references||row.shared_references||[]).join(", ")||this.t("no_refs"))+'</p>'+(editor.review?'<p class="value">'+escape(editor.review.value)+'</p>':"")+'<div class="actions"><button data-add '+(editor.windows.length>=6?"disabled":"")+'>'+escape(this.t("add"))+'</button><button data-preview '+(this.pending?"disabled":"")+'>'+escape(this.t("preview"))+'</button><button data-save class="primary" '+(!editor.review||this.pending?"disabled":"")+'>'+escape(this.t("apply"))+'</button><button data-cancel>'+escape(this.t("cancel"))+'</button></div></section>';
  }
  async action(row,value,preview=false){
    if(!row||!this.allowed(row))return;
    this.pending=true;
    this.notice=this.t("working");
    this.render();
    try{
      const result=await this._hass.callWS({type:"deltasol/dashboard/action",entry_id:this.context,entity_id:row.entity_id,value,preview});
      if(preview&&this.editor){this.editor.review=result;this.notice="";}
      else{this.notice=this.t(result.outcome)+(result.value==null?"":": "+result.value);this.editor=null;}
    }catch{this.notice=this.t("error");}
    finally{this.pending=false;this.render();}
  }
  async openHistory(row){
    if(!row)return;
    this.history={name:row.name,items:null};
    this.render();
    try{
      const end=new Date(), start=new Date(end.getTime()-86400000);
      const result=await this._hass.callWS({type:"history/history_during_period",start_time:start.toISOString(),end_time:end.toISOString(),entity_ids:[row.entity_id],minimal_response:true,no_attributes:true,significant_changes_only:false});
      if(this.history?.name===row.name)this.history.items=result[row.entity_id]||[];
    }catch{this.notice=this.t("error");if(this.history)this.history.items=[];}
    this.render();
  }
  historyView(){
    const h=this.history;
    return '<section class="card"><div class="toolbar"><h2 class="grow">'+escape(this.t("history")+' — '+h.name)+'</h2><button data-close-history>'+escape(this.t("close"))+'</button></div>'+(h.items===null?'<p>'+escape(this.t("loading"))+'</p>':h.items.length?'<div class="technical"><table><tbody>'+h.items.slice(-200).reverse().map(item=>{const ts=item.lu??item.last_updated??item.last_changed;const date=typeof ts==="number"?new Date(ts*1000):new Date(ts);return '<tr><td>'+escape(Number.isNaN(date.getTime())?"":date.toLocaleString(this._hass?.language))+'</td><td>'+escape(item.s??item.state)+'</td></tr>';}).join("")+'</tbody></table></div>':'<p>'+escape(this.t("no_history"))+'</p>')+'</section>';
  }
  change(event){
    const target=event.target;
    if(target.id==="installation"){this.context=target.value;this.object="";this.editor=null;this.history=null;this.report=null;if(!this.tabs().includes(this.tab))this.tab="overview";this.render();if(this.tab==="engineering")this.engineering();}
    if(target.id==="object"){this.object=target.value;this.render();}
    if(target.dataset.window!==undefined&&this.editor){this.editor.windows[Number(target.dataset.window)][Number(target.dataset.edge)]=target.value;this.editor.review=null;this.render();}
  }
  click(event){
    const button=event.target.closest("button");if(!button||button.disabled)return;
    const d=button.dataset;
    if(d.refresh!==undefined){this.load();if(this.tab==="engineering")this.engineering();}
    else if(d.tab){this.tab=d.tab;this.editor=null;this.history=null;this.notice="";this.render();if(this.tab==="engineering")this.engineering();}
    else if(d.object){this.object=d.object;this.render();}
    else if(d.history){this.openHistory(this.row(d.history));}
    else if(d.closeHistory!==undefined){this.history=null;this.render();}
    else if(d.edit){this.edit(this.row(d.edit));}
    else if(d.action){this.action(this.row(d.action),JSON.parse(d.value));}
    else if(d.apply){const input=Array.from(this.shadowRoot.querySelectorAll("[data-input]")).find(node=>node.dataset.input===d.apply);const row=this.row(d.apply);if(input&&row&&input.reportValidity())this.action(row,row.kind==="number"?Number(input.value):input.value);}
    else if(d.add!==undefined&&this.editor){this.editor.windows.push(["08:00","09:00"]);this.editor.review=null;this.render();}
    else if(d.remove!==undefined&&this.editor){this.editor.windows.splice(Number(d.remove),1);this.editor.review=null;this.render();}
    else if(d.cancel!==undefined){this.editor=null;this.render();}
    else if(d.preview!==undefined&&this.editor){this.action(this.row(this.editor.id),this.editor.windows.map(pair=>pair.join("-")).join(", "),true);}
    else if(d.save!==undefined&&this.editor?.review){this.action(this.row(this.editor.id),this.editor.review.value);}
    else if(d.export!==undefined&&this.report){const url=URL.createObjectURL(new Blob([JSON.stringify(this.report,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download="resol-diagnostics.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),0);}
  }
}
if(!customElements.get("deltasol-dashboard"))customElements.define("deltasol-dashboard",ResolDashboard);

/* RESOL single native HA dashboard. No protocol or business logic here. */
const COPY = {
  en: {overview:"Overview",solar:"Solar",heating:"Heating",hot_water:"Hot water",energy:"Energy & history",settings:"Settings",engineering:"Engineering",schedule:"Weekly schedules",refresh:"Refresh view",installation:"Installation",all:"All objects",unknown:"Unknown",unavailable:"Unavailable",empty:"No observed capabilities in this view.",error:"Request failed or uncertain. Check the current value.",loading:"Loading RESOL…",apply:"Apply",preview:"Review",cancel:"Cancel",add:"Add interval",remove:"Remove",history:"History",observed:"Observed and confirmed",submitted:"Submitted; check discovery progress",not_confirmed:"Outcome not confirmed",shared:"Shared program references",no_refs:"No references reported",configure:"Integration settings",configure_help:"Manage dashboard, Engineering access and gateway credentials in native Home Assistant settings.",search:"Search technical evidence",export:"Export redacted diagnostics",diagnostics:"Diagnostics",inventory:"Parameter inventory",none:"No captured parameters",close:"Close",time:"Last updated",no_history:"No recorded history",start:"Start",end:"End",confirm_schedule:"Review shared effects before saving. Each day is saved independently.",choose:"Choose an object",attention:"Needs attention",ready:"Observed",operation:"Operation",preferences:"Preferences",status:"Status",details:"Details",readonly:"Read only",back:"Back",enabled:"Enabled",select:"Select",on:"On",off:"Off",working:"Working…"},
  nl: {overview:"Overzicht",solar:"Zonne-energie",heating:"Verwarming",hot_water:"Warm water",energy:"Energie & historiek",settings:"Instellingen",engineering:"Engineering",schedule:"Weekprogramma’s",refresh:"Weergave vernieuwen",installation:"Installatie",all:"Alle objecten",unknown:"Onbekend",unavailable:"Niet beschikbaar",empty:"Geen waargenomen functies in deze weergave.",error:"Opdracht geweigerd of onzeker. Controleer de actuele waarde.",loading:"RESOL laden…",apply:"Toepassen",preview:"Controleren",cancel:"Annuleren",add:"Interval toevoegen",remove:"Verwijderen",history:"Historiek",observed:"Waargenomen en bevestigd",submitted:"Verstuurd; controleer de voortgang",not_confirmed:"Resultaat niet bevestigd",shared:"Gedeelde programmaverwijzingen",no_refs:"Geen verwijzingen gemeld",configure:"Integratie-instellingen",configure_help:"Beheer dashboard, Engineering en gatewaygegevens in de standaardinstellingen van Home Assistant.",search:"Technische informatie zoeken",export:"Geanonimiseerde diagnose exporteren",diagnostics:"Diagnose",inventory:"Parameterinventaris",none:"Geen parameters vastgelegd",close:"Sluiten",time:"Laatst bijgewerkt",no_history:"Geen geregistreerde historiek",start:"Begin",end:"Einde",confirm_schedule:"Controleer de gedeelde gevolgen voor je opslaat. Elke dag wordt afzonderlijk opgeslagen.",choose:"Kies een object",attention:"Aandacht nodig",ready:"Waargenomen",operation:"Bediening",preferences:"Voorkeuren",status:"Status",details:"Details",readonly:"Alleen lezen",back:"Terug",enabled:"Ingeschakeld",select:"Selecteren",on:"Aan",off:"Uit",working:"Bezig…"},
  fr: {overview:"Vue d’ensemble",solar:"Solaire",heating:"Chauffage",hot_water:"Eau chaude",energy:"Énergie et historique",settings:"Paramètres",engineering:"Ingénierie",schedule:"Programmes hebdomadaires",refresh:"Actualiser la vue",installation:"Installation",all:"Tous les objets",unknown:"Inconnu",unavailable:"Indisponible",empty:"Aucune fonction observée dans cette vue.",error:"Action refusée ou incertaine. Vérifiez la valeur actuelle.",loading:"Chargement de RESOL…",apply:"Appliquer",preview:"Vérifier",cancel:"Annuler",add:"Ajouter une plage",remove:"Supprimer",history:"Historique",observed:"Observé et confirmé",submitted:"Envoyé ; vérifiez la progression",not_confirmed:"Résultat non confirmé",shared:"Références du programme partagé",no_refs:"Aucune référence signalée",configure:"Paramètres de l’intégration",configure_help:"Gérez le tableau de bord, l’ingénierie et les accès dans les paramètres natifs de Home Assistant.",search:"Rechercher des données techniques",export:"Exporter le diagnostic expurgé",diagnostics:"Diagnostic",inventory:"Inventaire des paramètres",none:"Aucun paramètre capturé",close:"Fermer",time:"Dernière mise à jour",no_history:"Aucun historique enregistré",start:"Début",end:"Fin",confirm_schedule:"Vérifiez les effets partagés avant d’enregistrer. Chaque jour est enregistré séparément.",choose:"Choisir un objet",attention:"Attention requise",ready:"Observé",operation:"Commande",preferences:"Préférences",status:"État",details:"Détails",readonly:"Lecture seule",back:"Retour",enabled:"Activé",select:"Choisir",on:"Activé",off:"Désactivé",working:"En cours…"}
};
const STATUS_COPY = {
  en:{solid_fuel:"Solid-fuel heating",function:"Controller function",store:"Solar store",collector:"Solar collector",circuit:"Heating circuit",heating_function:"Heating function",meter:"Heat meter",solar_function:"Solar function",telemetry_ok:"Telemetry available",telemetry_bad:"Telemetry unavailable",catalogue_pending:"Firmware capabilities not yet confirmed",selectors:"Live function discovery",checkpoint:"Historical discovery needs attention; live capabilities are checked separately.",available:"Available",not_confirmed:"Not currently confirmed",controls:"Controls",summary:"Installation status"},
  nl:{solid_fuel:"Vastebrandstofverwarming",function:"Controllerfunctie",store:"Zonneboiler",collector:"Zonnecollector",circuit:"Verwarmingscircuit",heating_function:"Verwarmingsfunctie",meter:"Warmtemeter",solar_function:"Zonnefunctie",telemetry_ok:"Telemetrie beschikbaar",telemetry_bad:"Telemetrie niet beschikbaar",catalogue_pending:"Firmwarecapabilities nog niet bevestigd",selectors:"Live functiedetectie",checkpoint:"Historische discovery vraagt aandacht; live capabilities worden afzonderlijk gecontroleerd.",available:"Beschikbaar",not_confirmed:"Momenteel niet bevestigd",controls:"Bediening",summary:"Installatiestatus"},
  fr:{solid_fuel:"Chauffage à combustible solide",function:"Fonction du régulateur",store:"Ballon solaire",collector:"Capteur solaire",circuit:"Circuit de chauffage",heating_function:"Fonction de chauffage",meter:"Compteur de chaleur",solar_function:"Fonction solaire",telemetry_ok:"Télémétrie disponible",telemetry_bad:"Télémétrie indisponible",catalogue_pending:"Fonctions du micrologiciel non confirmées",selectors:"Détection des fonctions en direct",checkpoint:"La découverte historique nécessite une vérification ; les fonctions actuelles sont contrôlées séparément.",available:"Disponible",not_confirmed:"Non confirmé actuellement",controls:"Commandes",summary:"État de l’installation"}
};
const ENGINEERING_COPY={en:{live:"Live",capture:"Historical capture",comparison:"Comparison",changes:"changes",transport:"Transport and source evidence",filter:"Filter parameters",readonly:"Not verified writable",object:"Selected object",next:"Next"},nl:{live:"Actueel",capture:"Historische snapshot",comparison:"Vergelijking",changes:"wijzigingen",transport:"Transport en bronbewijs",filter:"Parameters filteren",readonly:"Niet bevestigd schrijfbaar",object:"Geselecteerd object",next:"Volgende"},fr:{live:"En direct",capture:"Capture historique",comparison:"Comparaison",changes:"modifications",transport:"Transport et preuves source",filter:"Filtrer les paramètres",readonly:"Écriture non confirmée",object:"Objet sélectionné",next:"Suivant"}};
const WEEK_COPY={en:{program:"Weekly program",Mo:"Monday",Di:"Tuesday",Mi:"Wednesday",Do:"Thursday",Fr:"Friday",Sa:"Saturday",So:"Sunday",unsupported:"Structural commissioning and SET/JobParam import are not supported.",no_intervals:"No active intervals",reference:"Known references; full ownership is checked on save."},nl:{program:"Weekprogramma",Mo:"Maandag",Di:"Dinsdag",Mi:"Woensdag",Do:"Donderdag",Fr:"Vrijdag",Sa:"Zaterdag",So:"Zondag",unsupported:"Structurele inbedrijfstelling en SET/JobParam-import worden niet ondersteund.",no_intervals:"Geen actieve intervallen",reference:"Bekende verwijzingen; volledig eigenaarschap wordt bij opslaan gecontroleerd."},fr:{program:"Programme hebdomadaire",Mo:"Lundi",Di:"Mardi",Mi:"Mercredi",Do:"Jeudi",Fr:"Vendredi",Sa:"Samedi",So:"Dimanche",unsupported:"La mise en service structurelle et l’import SET/JobParam ne sont pas pris en charge.",no_intervals:"Aucun intervalle actif",reference:"Références connues ; les liens sont vérifiés lors de l’enregistrement."}};
const OBJECT_COPY={en:{mode:"Operating mode",target:"Target",configured_activity:"Configured activity",attention:"Needs attention",measurements:"Measurements",controls:"Controls",preferences:"Operating preferences",linked:"Linked weekly programs",unconfirmed:"No confirmed measurement for this object",status:"Status",history:"History",settings:"Settings",engineering:"Engineering"},nl:{mode:"Bedrijfsmodus",target:"Doelwaarde",configured_activity:"Ingestelde activiteit",attention:"Aandacht nodig",measurements:"Metingen",controls:"Bediening",preferences:"Bedrijfsinstellingen",linked:"Gekoppelde weekprogramma’s",unconfirmed:"Geen bevestigde meting voor dit object",status:"Status",history:"Historiek",settings:"Instellingen",engineering:"Engineering"},fr:{mode:"Mode de fonctionnement",target:"Consigne",configured_activity:"Activité configurée",attention:"Attention requise",measurements:"Mesures",controls:"Commandes",preferences:"Préférences de fonctionnement",linked:"Programmes hebdomadaires liés",unconfirmed:"Aucune mesure confirmée pour cet objet",status:"État",history:"Historique",settings:"Paramètres",engineering:"Ingénierie"}};
const LIVE_COPY={en:{telemetry_unavailable:"Telemetry connection unavailable",parameter_unavailable:"Parameter connection unavailable",requirements_not_confirmed:"Installed function or dependencies not confirmed",last_observed:"Last observed",parameter_pending:"Parameter access not confirmed",range:"Period",day:"24 hours",week:"7 days",month:"30 days",history_error:"History could not be loaded",recorded_values:"Recorded values",source:"Source and references"},nl:{telemetry_unavailable:"Telemetrieverbinding niet beschikbaar",parameter_unavailable:"Parameterverbinding niet beschikbaar",requirements_not_confirmed:"Geïnstalleerde functie of afhankelijkheden niet bevestigd",last_observed:"Laatst waargenomen",parameter_pending:"Parametertoegang niet bevestigd",range:"Periode",day:"24 uur",week:"7 dagen",month:"30 dagen",history_error:"Historiek kon niet worden geladen",recorded_values:"Geregistreerde waarden",source:"Bron en verwijzingen"},fr:{telemetry_unavailable:"Connexion télémétrique indisponible",parameter_unavailable:"Connexion paramètres indisponible",requirements_not_confirmed:"Fonction installée ou dépendances non confirmées",last_observed:"Dernière observation",parameter_pending:"Accès aux paramètres non confirmé",range:"Période",day:"24 heures",week:"7 jours",month:"30 jours",history_error:"Impossible de charger l’historique",recorded_values:"Valeurs enregistrées",source:"Source et références"}};
const RELATION_COPY={en:{relations:"Native assignments",sensor_reference:"Sensor reference",relay_assignment:"Assigned relay",relay_dependency:"Referenced relay",analog_assignment:"Assigned analogue output",analog_dependency:"Referenced analogue output",native_reference_only:"Native reference; source measurement not confirmed",open_source:"Open source device",assignment_help:"Configured relationships, not proof of an active pump or heat flow."},nl:{relations:"Native toewijzingen",sensor_reference:"Sensorreferentie",relay_assignment:"Toegewezen relais",relay_dependency:"Gerefereerd relais",analog_assignment:"Toegewezen analoge uitgang",analog_dependency:"Gerefereerde analoge uitgang",native_reference_only:"Native referentie; bronmeting niet bevestigd",open_source:"Open bronapparaat",assignment_help:"Ingestelde relaties, geen bewijs van een actieve pomp of warmtestroom."},fr:{relations:"Affectations natives",sensor_reference:"Référence capteur",relay_assignment:"Relais affecté",relay_dependency:"Relais référencé",analog_assignment:"Sortie analogique affectée",analog_dependency:"Sortie analogique référencée",native_reference_only:"Référence native ; mesure source non confirmée",open_source:"Ouvrir l’appareil source",assignment_help:"Relations configurées, sans preuve de pompe active ou de flux thermique."}};
const TASK_COPY={en:{attention_title:"Needs attention",connection_action:"Check gateway connection and integration settings.",parameter_action:"Check parameter access and allow discovery to recover.",requirements_action:"Check installed function and its prerequisites in Settings or Engineering.",unknown_action:"Check the native source device.",alarm_action:"Inspect the reported condition before changing settings.",view_object:"View object",all_clear:"No current attention items among the visible operational capabilities.",routine_controls:"Daily controls",no_routine_controls:"No confirmed daily control available for this selection.",range_limits:"Allowed range",increment:"Step",not_writable:"Read only or not permitted",back_overview:"All objects",more_preferences:"View all operating preferences"},nl:{attention_title:"Aandacht nodig",connection_action:"Controleer de gatewayverbinding en integratie-instellingen.",parameter_action:"Controleer parametertoegang en laat functiedetectie herstellen.",requirements_action:"Controleer de geïnstalleerde functie en voorwaarden in Instellingen of Engineering.",unknown_action:"Controleer het native bronapparaat.",alarm_action:"Controleer de gemelde toestand voordat je instellingen wijzigt.",view_object:"Bekijk object",all_clear:"Geen actuele aandachtspunten bij de zichtbare operationele functies.",routine_controls:"Dagelijkse bediening",no_routine_controls:"Geen bevestigde dagelijkse bediening voor deze selectie.",range_limits:"Toegestaan bereik",increment:"Stap",not_writable:"Alleen lezen of geen toestemming",back_overview:"Alle objecten",more_preferences:"Bekijk alle bedrijfsinstellingen"},fr:{attention_title:"Attention requise",connection_action:"Vérifiez la connexion à la passerelle et les paramètres de l’intégration.",parameter_action:"Vérifiez l’accès aux paramètres et laissez la détection se rétablir.",requirements_action:"Vérifiez la fonction installée et ses prérequis dans les paramètres ou l’ingénierie.",unknown_action:"Vérifiez l’appareil source natif.",alarm_action:"Examinez la condition signalée avant de modifier les paramètres.",view_object:"Voir l’objet",all_clear:"Aucun point d’attention actuel parmi les fonctions opérationnelles visibles.",routine_controls:"Commandes quotidiennes",no_routine_controls:"Aucune commande quotidienne confirmée pour cette sélection.",range_limits:"Plage autorisée",increment:"Pas",not_writable:"Lecture seule ou accès non autorisé",back_overview:"Tous les objets",more_preferences:"Voir toutes les préférences"}};
const DAYS=["Mo","Di","Mi","Do","Fr","Sa","So"];
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
    this.filter = "all";
    this.page = 0;
    this.editor = null;
    this.history = null;
    this.shadowRoot.addEventListener("click", event => this.click(event));
    this.shadowRoot.addEventListener("change", event => this.change(event));
    this.shadowRoot.addEventListener("input", event => {
      if(event.target.id==="technical-search"){this.query=event.target.value;this.page=0;this.renderInventory();}
    });
  }
  connectedCallback(){this.render();if(this._hass&&!this.loaded)this.load();this.subscribeRegistry();}
  disconnectedCallback(){this.unsubscribeRegistry?.();this.unsubscribeRegistry=null;clearTimeout(this.registryTimer);}
  subscribeRegistry(){
    if(this.unsubscribeRegistry||this.registrySubscription||!this._hass?.connection?.subscribeEvents)return;
    this.registrySubscription=true;
    this._hass.connection.subscribeEvents(()=>{
      clearTimeout(this.registryTimer);
      this.registryTimer=setTimeout(()=>{if(this.isConnected)this.load();},250);
    },"entity_registry_updated").then(unsubscribe=>{
      this.registrySubscription=false;
      if(this.isConnected)this.unsubscribeRegistry=unsubscribe;else unsubscribe();
    }).catch(()=>{this.registrySubscription=false;});
  }
  set hass(hass) {
    const previous=this._hass;
    this._hass=hass;
    if(!this.isConnected)return;
    this.subscribeRegistry();
    if(!this.loaded&&!this.loading){this.load();return;}
    if(this.editor||this.pending||this.shadowRoot.activeElement?.matches("input,select,textarea"))return;
    const rows=this.rows();
    if(rows.some(row=>previous?.states?.[row.entity_id]?.state!==hass.states?.[row.entity_id]?.state&&["unknown","unavailable",undefined].includes(previous?.states?.[row.entity_id]?.state)!==["unknown","unavailable",undefined].includes(hass.states?.[row.entity_id]?.state))){this.load();return;}
    if(rows.some(row=>row.kind==="sensor"&&row.depth==="engineering"&&previous?.states?.[row.entity_id]!==hass.states?.[row.entity_id])){this.load();return;}
    if(previous?.language!==hass.language||rows.some(row=>previous?.states?.[row.entity_id]!==hass.states?.[row.entity_id])){
      this.render();
    }
  }
  t(key){const lang=(this._hass?.language||"en").slice(0,2);return (COPY[lang]||COPY.en)[key]||(STATUS_COPY[lang]||STATUS_COPY.en)[key]||(ENGINEERING_COPY[lang]||ENGINEERING_COPY.en)[key]||COPY.en[key]||STATUS_COPY.en[key]||ENGINEERING_COPY.en[key]||(WEEK_COPY[lang]||WEEK_COPY.en)[key]||WEEK_COPY.en[key]||(OBJECT_COPY[lang]||OBJECT_COPY.en)[key]||OBJECT_COPY.en[key]||(LIVE_COPY[lang]||LIVE_COPY.en)[key]||LIVE_COPY.en[key]||(RELATION_COPY[lang]||RELATION_COPY.en)[key]||RELATION_COPY.en[key]||(TASK_COPY[lang]||TASK_COPY.en)[key]||TASK_COPY.en[key]||key;}
  current(){return this.installations.find(item=>item.id===this.context);}
  rows(){return this.current()?.objects.flatMap(asset=>asset.capabilities)||[];}
  row(id){return this.rows().find(row=>row.entity_id===id);}
  async load(){
    if(!this._hass||this.loading)return;
    this.loading=true;
    try{
      this.installations=await this._hass.callWS({type:"deltasol/dashboard/list"});
      if(!this.current()){this.context=this.installations[0]?.id||"";this.object="";this.report=null;}
      if(!this.current()?.objects.some(asset=>asset.id===this.object))this.object="";
      if(!this.current()?.engineering){this.report=null;this.query="";this.filter="all";this.page=0;}
      if(!this.tabs().includes(this.tab))this.tab="overview";
      if(this.editor&&!this.row(this.editor.id)?.control)this.editor=null;
      if(this.history&&!this.row(this.history.id))this.history=null;
      this.loaded=true;
    }catch{this.installations=[];this.context="";this.object="";this.report=null;this.editor=null;this.history=null;this.tab="overview";this.notice=this.t("error");this.loaded=true;}
    finally{this.loading=false;if(!this.editor&&!this.pending&&!this.shadowRoot.activeElement?.matches("input,select,textarea"))this.render();}
  }
  tabs(){
    const installation=this.current();
    const groups=new Set(this.rows().map(row=>row.section));
    return SECTIONS.filter(tab=>tab==="overview"||tab==="energy"||groups.has(tab)||tab==="settings"&&installation?.settings||tab==="engineering"&&installation?.engineering);
  }
  state(row){return this._hass?.states?.[row.entity_id];}
  capabilityLabel(row){
    if(row.renamed)return row.name;
    const lang=(this._hass?.language||"en").slice(0,2);
    const names={
      nl:{"configured mode":"Bedrijfsmodus","configured mode (including coupled circuits)":"Bedrijfsmodus (inclusief gekoppelde circuits)","day correction":"Dagcorrectie","night correction":"Nachtcorrectie","heating curve":"Stooklijn","thermostat 1 on":"Thermostaat 1 inschakeltemperatuur","thermostat 1 off":"Thermostaat 1 uitschakeltemperatuur","thermostat 2 on":"Thermostaat 2 inschakeltemperatuur","thermostat 2 off":"Thermostaat 2 uitschakeltemperatuur","automatic daylight saving time":"Automatische zomertijd","power":"Vermogen","temperature":"Temperatuur","energy":"Energie"},
      fr:{"configured mode":"Mode configuré","configured mode (including coupled circuits)":"Mode configuré (circuits couplés inclus)","day correction":"Correction de jour","night correction":"Correction de nuit","heating curve":"Courbe de chauffe","thermostat 1 on":"Thermostat 1 température d’enclenchement","thermostat 1 off":"Thermostat 1 température d’arrêt","thermostat 2 on":"Thermostat 2 température d’enclenchement","thermostat 2 off":"Thermostat 2 température d’arrêt","automatic daylight saving time":"Heure d’été automatique","power":"Puissance","temperature":"Température","energy":"Énergie"}
    };
    if(row.schedule&&row.program&&DAYS.includes(row.day))return this.t("program")+" "+row.program+" · "+this.t(row.day);
    const label=row.native_label||row.name||"RESOL";
    const match=label.match(/^(Heating circuit|Function block|Arrangement function|Solar store|Solar collector|Heating function) (\d+) (.+)$/i);
    if(match){
      const type={"heating circuit":"circuit","function block":"function","arrangement function":"function","solar store":"store","solar collector":"collector","heating function":"heating_function"}[match[1].toLowerCase()];
      return this.t(type)+" "+match[2]+" · "+(names[lang]?.[match[3].toLowerCase()]||match[3]);
    }
    return names[lang]?.[label.toLowerCase()]||label;
  }
  optionLabel(value){
    const lang=(this._hass?.language||"en").slice(0,2);
    const modes={nl:{Auto:"Auto",Day:"Dag",Night:"Nacht",Summer:"Zomer",Holiday:"Vakantie",Off:"Uit",On:"Aan",Min:"Minimum",Max:"Maximum"},fr:{Auto:"Auto",Day:"Jour",Night:"Nuit",Summer:"Été",Holiday:"Vacances",Off:"Arrêt",On:"Marche",Min:"Minimum",Max:"Maximum"}};
    return modes[lang]?.[value]||value;
  }
  value(row){
    const state=this.state(row);
    if(!state||state.state==="unknown"||state.state==="unavailable"||row.availability&&row.availability!=="available")return this.t(state?.state==="unavailable"||row.availability&&row.availability!=="available"?"unavailable":"unknown");
    const unit=state.attributes.unit_of_measurement;
    return (row.kind==="select"||row.kind==="switch"?this.optionLabel(row.kind==="switch"?(state.state==="on"?"On":"Off"):state.state):state.state)+(unit?" "+unit:"");
  }
  valid(row){const state=this.state(row);return Boolean(state&&state.state!=="unknown"&&state.state!=="unavailable"&&(!row.availability||row.availability==="available"));}
  allowed(row){return row.control&&(this.valid(row)||row.kind==="button"&&this.state(row)?.state==="unknown")&&!this.pending;}
  control(row){
    const id=escape(row.entity_id), state=this.state(row), attrs=state?.attributes||{},disabled=this.allowed(row)?"":"disabled";
    if(!row.control)return "";
    if(row.schedule)return '<button data-edit="'+id+'" '+disabled+'>'+escape(this.t("preview"))+'</button>';
    if(row.kind==="switch")return '<button data-action="'+id+'" data-value="'+(state?.state!=="on")+'" '+disabled+'>'+escape(this.t(state?.state==="on"?"off":"on"))+'</button>';
    if(row.kind==="button")return '<button data-action="'+id+'" data-value="null" '+disabled+'>'+escape(this.t("apply"))+'</button>';
    if(row.kind==="select")return '<select aria-label="'+escape(this.capabilityLabel(row))+'" data-input="'+id+'" '+disabled+'>'+ (attrs.options||[]).map(v=>'<option value="'+escape(v)+'" '+(state?.state===v?"selected":"")+'>'+escape(this.optionLabel(v))+'</option>').join("")+'</select><button data-apply="'+id+'" '+disabled+'>'+escape(this.t("apply"))+'</button>';
    if(row.kind==="number"||row.kind==="text"){
      const type=row.kind==="number"?"number":"text";
      const limits=type==="number"?["min","max","step"].map(k=>attrs[k]===undefined?"":" "+k+'="'+escape(attrs[k])+'"').join(""):"";
      return '<input aria-label="'+escape(this.capabilityLabel(row))+'" data-input="'+id+'" type="'+type+'" value="'+escape(state?.state||"")+'" '+limits+' '+disabled+'><button data-apply="'+id+'" '+disabled+'>'+escape(this.t("apply"))+'</button>';
    }
    return "";
  }
  detail(row,control){
    const state=this.state(row),observed=state?.attributes?.["Last Updated"]||(row.depth!=="status"?this.current()?.health?.parameter_observed_at:null);
    const updated=observed?new Date(observed).toLocaleString(this._hass?.language):"";
    const reason=row.availability&&row.availability!=="available"?this.t(row.availability):"";
    const attrs=state?.attributes||{};
    const limits=control&&row.kind==="number"&&Number.isFinite(attrs.min)&&Number.isFinite(attrs.max)?'<small style="display:block">'+escape(this.t("range_limits")+": "+attrs.min+" – "+attrs.max+(attrs.unit_of_measurement?" "+attrs.unit_of_measurement:"")+(Number.isFinite(attrs.step)?" · "+this.t("increment")+" "+attrs.step:""))+'</small>':"";
    const access=control&&["number","select","switch","text"].includes(row.kind)&&!row.control&&this.valid(row)?'<small>'+escape(this.t("not_writable"))+'</small>':"";
    const evidence=this.tab==="engineering"&&row.parameter?'<details><summary>'+escape(this.t("source"))+'</summary><pre>'+escape(JSON.stringify({entity_id:row.entity_id,parameter:row.parameter,index:row.reference_index,source_address:row.source_address,controller_address:row.controller_address,references:row.live_reference_context},null,2))+'</pre></details>':"";
    return '<div class="line"><div class="grow"><div>'+escape(this.capabilityLabel(row))+'</div><div class="value'+(this.valid(row)?"":" warning")+'">'+escape(this.value(row))+'</div>'+(reason?'<small class="warning">'+escape(reason)+'</small>':"")+(updated?'<small style="display:block">'+escape(this.t("last_observed"))+': '+escape(updated)+'</small>':"")+limits+access+evidence+'</div><div class="actions">'+(control?this.control(row):"")+'<button data-history="'+escape(row.entity_id)+'" aria-label="'+escape(this.t("history")+' '+this.capabilityLabel(row))+'">'+escape(this.t("history"))+'</button></div></div>';
  }
  visibleRows(asset){
    const rows=asset.capabilities;
    if(this.tab==="settings")return rows.filter(row=>row.depth==="settings"||row.depth==="controls");
    if(this.tab==="engineering")return rows.filter(row=>row.depth==="engineering");
    if(this.tab==="overview"){
      const operational=rows.filter(row=>row.depth==="status"||row.depth==="controls"||row.role==="configured_activity");
      return operational.length&&!this.object?operational:rows.filter(row=>row.depth!=="engineering");
    }
    return rows.filter(row=>row.section===this.tab&&row.depth!=="engineering");
  }
  objectTitle(asset){
    const type=asset.object_kind?this.t(asset.object_kind)+" "+asset.object_number:"";
    return type&&!asset.renamed&&asset.name.startsWith("RESOL ")?type:asset.name;
  }
  attentionItems(){
    const current=this.current(),items=[];
    if(!current)return items;
    const h=current.health;
    if(h?.telemetry===false)return [{reason:"telemetry_unavailable",action:"connection_action",asset:null,rows:[]}];
    if(h?.parameter_access&&h.parameter_current===false)items.push({reason:"parameter_unavailable",action:"parameter_action",asset:null,rows:[]});
    for(const asset of current.objects){
      if(this.object&&asset.id!==this.object)continue;
      const groups=new Map();
      for(const row of asset.capabilities){
        if(row.depth!=="status"&&row.depth!=="controls"&&row.role!=="configured_activity")continue;
        let reason;
        if(row.role==="attention"&&this.valid(row)&&this.state(row)?.state==="on")reason="alarm_action";
        else if(!this.valid(row))reason=row.availability&&row.availability!=="available"?row.availability:"unknown";
        else continue;
        if(reason==="parameter_unavailable"&&h?.parameter_current===false)continue;
        if(!groups.has(reason))groups.set(reason,[]);
        groups.get(reason).push(row);
      }
      for(const [reason,rows] of groups){
        const action=reason==="alarm_action"?"alarm_action":reason==="requirements_not_confirmed"?"requirements_action":reason==="telemetry_unavailable"?"connection_action":reason==="parameter_unavailable"?"parameter_action":"unknown_action";
        items.push({reason,action,asset,rows});
      }
    }
    return items;
  }
  attentionView(){
    const items=this.attentionItems();
    if(!items.length)return '<section class="card"><h2>'+escape(this.t("attention_title"))+'</h2><p class="muted">'+escape(this.t("all_clear"))+'</p></section>';
    return '<section class="card"><h2>'+escape(this.t("attention_title"))+'</h2>'+items.map(item=>'<div class="line"><div class="grow"><strong class="warning">'+escape(item.asset?this.objectTitle(item.asset):this.t(item.reason))+'</strong>'+(item.rows.length?'<div>'+escape(item.rows.map(row=>this.capabilityLabel(row)).join(", "))+'</div>':"")+'<p class="muted">'+escape(this.t(item.action))+'</p></div>'+(item.asset&&this.object!==item.asset.id?'<button data-source-object="'+escape(item.asset.id)+'">'+escape(this.t("view_object"))+'</button>':"")+'</div>').join("")+'</section>';
  }
  routineControls(){
    const assets=(this.current()?.objects||[]).filter(asset=>!this.object||asset.id===this.object);
    const rows=assets.flatMap(asset=>asset.capabilities.filter(row=>row.depth==="controls"&&this.allowed(row)&&(this.tab==="overview"||row.section===this.tab)).map(row=>({asset,row})));
    return rows.sort((a,b)=>(b.row.priority||0)-(a.row.priority||0)||String(a.asset.name).localeCompare(String(b.asset.name)));
  }
  routineView(){
    const items=this.routineControls();
    if(!items.length)return "";
    return '<section class="card"><h2>'+escape(this.t("routine_controls"))+'</h2>'+items.map(({asset,row})=>'<div><small>'+escape(this.objectTitle(asset))+'</small>'+this.detail(row,true)+'</div>').join("")+'</section>';
  }
  summary(){
    const h=this.current()?.health;
    if(!h)return "";
    const rows=this.rows().filter(row=>row.depth!=="engineering");
    const available=rows.filter(row=>this.valid(row)).length;
    return '<section class="card" aria-label="'+escape(this.t("summary"))+'"><h2>'+escape(this.t("summary"))+'</h2><div class="grid"><div><strong class="'+(h.telemetry?"":"warning")+'">'+escape(this.t(h.telemetry?"telemetry_ok":"telemetry_bad"))+'</strong><p class="muted">'+available+' / '+rows.length+' '+escape(this.t("available"))+'</p></div>'+(h.parameter_access?'<div><strong>'+escape(this.t("selectors"))+'</strong><p>'+h.selectors_observed+' / '+h.selectors_total+'</p>'+(!h.catalogue?'<p class="warning">'+escape(this.t("catalogue_pending"))+'</p>':"")+(h.selectors_total?'<progress max="'+h.selectors_total+'" value="'+h.selectors_observed+'"></progress>':"")+(h.parameter_current===false?'<p class="warning">'+escape(this.t("parameter_pending"))+'</p>':"")+'</div>':"")+'</div>'+(h.checkpoint?'<p class="warning">'+escape(this.t("checkpoint"))+'</p>':"")+'</section>';
  }
  weekPrograms(){
    const assets=this.current()?.objects||[];
    const selected=assets.find(asset=>asset.id===this.object);
    const linked=new Set((selected?.capabilities||[]).map(row=>row.linked_program).filter(Boolean));
    const programs=new Map();
    for(const asset of assets)for(const row of asset.capabilities){
      if(!row.schedule||!row.program||!DAYS.includes(row.day))continue;
      if(this.object&&asset.id!==this.object&&!linked.has(row.program))continue;
      const key=asset.id+":"+row.program;
      if(!programs.has(key))programs.set(key,{number:row.program,days:new Map()});
      programs.get(key).days.set(row.day,row);
    }
    return [...programs.values()].sort((a,b)=>a.number-b.number);
  }
  weekView(editable=true){
    const programs=this.weekPrograms();
    if(!programs.length)return "";
    return '<h2 style="margin-top:24px">'+escape(this.t("schedule"))+'</h2><div class="grid">'+programs.map(program=>{
      const refs=[...new Set([...program.days.values()].flatMap(row=>row.shared_references||[]))];
      return '<section class="card"><h3>'+escape(this.t("program")+" "+program.number)+'</h3><p class="muted">'+escape(this.t("reference"))+' '+escape(refs.join(", ")||this.t("no_refs"))+'</p>'+DAYS.map(day=>{
        const row=program.days.get(day);
        if(!row)return '<div class="line"><span>'+escape(this.t(day))+'</span><span class="muted">'+escape(this.t("not_confirmed"))+'</span></div>';
        const value=this.state(row)?.state;
        const intervals=this.valid(row)&&value?value.split(",").map(interval=>interval.trim().split("-")):[];
        const bars=intervals.map(pair=>{
          const minutes=clock=>{const parts=clock?.split(":").map(Number)||[];return parts.length===2?parts[0]*60+parts[1]:NaN;};
          const a=minutes(pair[0]),b=minutes(pair[1]);
          return Number.isFinite(a)&&Number.isFinite(b)&&a>=0&&b<=1440&&b>a?'<span style="position:absolute;left:'+(a/14.4)+'%;width:'+((b-a)/14.4)+'%;height:100%;background:var(--primary-color)"></span>':"";
        }).join("");
        return '<div class="line"><div class="grow"><strong>'+escape(this.t(day))+'</strong><div aria-hidden="true" style="height:10px;position:relative;border-radius:4px;overflow:hidden;background:var(--divider-color);margin:8px 0">'+bars+'</div><small>'+escape(this.valid(row)?value||this.t("no_intervals"):this.t("not_confirmed"))+'</small></div>'+(editable?this.control(row):"")+'</div>';
      }).join("")+'</section>';
    }).join("")+'</div>';
  }
  prominentRows(rows){
    // Pick semantically useful facts rather than registry insertion order.
    // No invented values; unavailable facts stay visibly unavailable.
    const candidates=rows.filter(row=>row.depth==="status"||row.depth==="controls"||row.role==="configured_activity");
    const sorted=[...candidates].sort((a,b)=>(b.priority||0)-(a.priority||0)||String(a.name).localeCompare(String(b.name)));
    return sorted.slice(0,4);
  }
  relationView(asset){
    const relations=asset.relations||[];
    if(!relations.length)return "";
    return '<h3 style="margin-top:18px">'+escape(this.t("relations"))+'</h3><p class="muted">'+escape(this.t("assignment_help"))+'</p>'+relations.map(rel=>{
      const source=this.current()?.objects.find(item=>item.id===rel.source_object);
      return '<div class="line"><div class="grow"><strong>'+escape(this.t(rel.kind))+' '+escape(rel.number)+'</strong><div class="muted">'+escape(source?this.objectTitle(source):this.t("native_reference_only"))+'</div>'+(this.tab==="engineering"&&rel.parameter?'<small>'+escape(rel.parameter)+'</small>':"")+'</div>'+(source&&source.id!==asset.id?'<button data-source-object="'+escape(source.id)+'">'+escape(this.t("open_source"))+'</button>':"")+'</div>';
    }).join("");
  }
  objectDetail(rows){
    const groups=[
      ["measurements",rows.filter(row=>row.depth==="status")],
      ["controls",rows.filter(row=>row.depth==="controls")],
      ["preferences",rows.filter(row=>row.depth==="settings"&&!row.schedule)]
    ];
    return groups.filter(([,items])=>items.length).map(([key,items])=>key==="preferences"?'<details><summary>'+escape(this.t("preferences"))+' ('+items.length+')</summary>'+items.map(row=>this.detail(row,false)).join("")+'</details>':'<h3 style="margin-top:18px">'+escape(this.t(key))+'</h3>'+items.map(row=>this.detail(row,key==="controls")).join("")).join("");
  }
  cards(){
    const assets=(this.current()?.objects||[]).filter(asset=>!this.object||asset.id===this.object);
    const cards=assets.map(asset=>{
      const rows=this.visibleRows(asset).filter(row=>this.tab!=="settings"||!row.schedule||!row.program);
      if(!rows.length)return "";
      const available=rows.filter(row=>this.valid(row)).length;
      const header='<div class="toolbar"><div class="grow"><h2>'+escape(this.objectTitle(asset))+'</h2><small>'+available+' / '+rows.length+' '+escape(this.t("available"))+'</small></div>'+(this.object?"":'<button data-object="'+escape(asset.id)+'">'+escape(this.t("details"))+'</button>')+'</div>';
      if(this.tab==="overview"&&!this.object){
        const prominent=this.prominentRows(rows);
        return '<article class="card">'+header+prominent.map(row=>this.detail(row,false)).join("")+(!prominent.length?'<p class="muted">'+escape(this.t("unconfirmed"))+'</p>':"")+(available!==rows.length?'<p class="warning">'+(rows.length-available)+' '+escape(this.t("not_confirmed"))+'</p>':"")+'</article>';
      }
      const ordinary=this.tab!=="settings"&&this.tab!=="engineering";
      return '<article class="card">'+header+(ordinary?this.objectDetail(rows):rows.map(row=>this.detail(row,true)).join(""))+(this.object?this.relationView(asset):"")+((this.tab!=="settings"&&this.tab!=="engineering"&&this.object&&this.current()?.settings&&rows.some(row=>row.depth==="settings"))?'<button data-tab="settings">'+escape(this.t("more_preferences"))+'</button>':"")+'</article>';
    }).filter(Boolean);
    return cards.length?'<div class="grid">'+cards.join("")+'</div>':this.tab==="settings"&&this.weekPrograms().length?"":'<p class="muted">'+escape(this.t("empty"))+'</p>';
  }
  render(){
    if(!this.shadowRoot)return;
    const installation=this.current(), tabs=this.tabs(), assets=installation?.objects||[];
    const count=this.attentionItems().length;
    const options=this.installations.map(item=>'<option value="'+escape(item.id)+'" '+(item.id===this.context?"selected":"")+'>'+escape(item.name)+'</option>').join("");
    const objects=assets.map(asset=>'<option value="'+escape(asset.id)+'" '+(asset.id===this.object?"selected":"")+'>'+escape(this.objectTitle(asset))+'</option>').join("");
    this.shadowRoot.innerHTML='<style>'+style+'</style><main><header><ha-icon icon="mdi:solar-power"></ha-icon><h1 class="grow">RESOL</h1><select id="installation" aria-label="'+escape(this.t("installation"))+'">'+options+'</select><button data-refresh>'+escape(this.t("refresh"))+'</button></header><nav aria-label="RESOL">'+tabs.map(tab=>'<button data-tab="'+tab+'" class="'+(this.tab===tab?"active":"")+'" aria-current="'+(this.tab===tab?"page":"false")+'">'+escape(this.t(tab))+'</button>').join("")+'</nav><div role="status" class="notice">'+escape(this.notice)+'</div>'+(!this.loaded?'<p>'+escape(this.t("loading"))+'</p>':!installation?'<p>'+escape(this.t("empty"))+'</p>':'<div class="toolbar"><select id="object" aria-label="'+escape(this.t("choose"))+'"><option value="">'+escape(this.t("all"))+'</option>'+objects+'</select>'+(this.tab==="overview"?'<span class="'+(count?"warning":"muted")+'">'+escape(count?this.t("attention")+": "+count:this.t("status"))+'</span>':"")+'</div>'+(this.tab==="settings"?'<section class="card"><h2>'+escape(this.t("configure"))+'</h2><p>'+escape(this.t("configure_help"))+'</p><p class="muted">'+escape(this.t("unsupported"))+'</p><a href="/config/integrations/integration/deltasol">'+escape(this.t("configure"))+'</a></section>':"")+(this.tab==="engineering"?this.engineeringView():"")+(this.tab==="overview"?this.summary()+this.attentionView()+(!this.object?this.routineView():""):"")+this.cards()+(this.tab==="settings"?this.weekView():this.object&&this.tab!=="engineering"?this.weekView(false):""))+(this.editor?this.editorView():"")+(this.history?this.historyView():"")+'</main>';
    if(this.tab==="engineering")this.renderInventory();
  }
  async engineering(){
    this.report=null;
    this.render();
    const id=this.context;
    try{
      const report=await this._hass.callWS({type:"deltasol/dashboard/engineering",entry_id:id});
      if(this.context===id&&this.tab==="engineering"&&this.current()?.engineering)this.report=report;
    }catch{this.notice=this.t("error");}
    this.render();
  }
  engineeringView(){
    if(!this.report)return '<p>'+escape(this.t("loading"))+'</p>';
    const report=this.report, discovery=report.configuration_discovery||{},access=report.parameter_access||{};
    const summary={gateway:report.gateway,telemetry:report.telemetry,parameter_access:access,configuration_discovery:{status:discovery.status,checkpoint_error:discovery.checkpoint_error,coverage:discovery.coverage,counts:discovery.counts,started_at:discovery.started_at,finished_at:discovery.finished_at,catalogue_source:discovery.catalogue_source},operational_contracts_source:report.operational_contracts_source};
    const comparison=discovery.comparison||{};
    const changes=Object.keys(comparison.changes||{}).length;
    const object=this.current()?.objects.find(asset=>asset.id===this.object);
    return '<section class="card"><div class="toolbar"><h2 class="grow">'+escape(this.t("diagnostics"))+'</h2><button data-report-refresh>'+escape(this.t("refresh"))+'</button><button data-export>'+escape(this.t("export"))+'</button></div><div class="grid"><div><h3>'+escape(this.t("live"))+'</h3><p>'+escape(access.status||this.t("unknown"))+'</p><p>'+escape(access.reference_profile||"")+' · '+escape(access.discovery_changeset||"")+'</p></div><div><h3>'+escape(this.t("capture"))+'</h3><p>'+escape(discovery.status||this.t("unknown"))+'</p><p>'+escape(discovery.finished_at||discovery.started_at||"")+'</p></div><div><h3>'+escape(this.t("comparison"))+'</h3><p>'+escape(comparison.status||this.t("not_confirmed"))+' · '+changes+' '+escape(this.t("changes"))+'</p></div></div><details><summary>'+escape(this.t("transport"))+'</summary><pre>'+escape(JSON.stringify(summary,null,2))+'</pre></details><h2>'+escape(this.t("inventory"))+'</h2>'+(object?'<p>'+escape(this.objectTitle(object))+'</p>':"")+'<div class="toolbar"><input id="technical-search" aria-label="'+escape(this.t("search"))+'" placeholder="'+escape(this.t("search"))+'" value="'+escape(this.query)+'"><select id="technical-filter" aria-label="'+escape(this.t("filter"))+'">'+["all","live","readonly","changes","object"].map(key=>'<option value="'+key+'" '+(this.filter===key?"selected":"")+'>'+escape(this.t(key))+'</option>').join("")+'</select></div><div class="technical" id="inventory"></div></section>';
  }
  inventoryEntries(){
    const discovery=this.report?.configuration_discovery||{}, live=this.report?.configuration_controls||{}, contracts=this.report?.operational_contracts||{}, reference=this.report?.reference_inventory||{}, history=discovery.parameters||{}, changes=discovery.comparison?.changes||{};
    const selected=new Set((this.current()?.objects.find(asset=>asset.id===this.object)?.capabilities||[]).map(row=>row.parameter).filter(Boolean));
    const rows=[];
    for(const key of new Set([...Object.keys(history),...Object.keys(contracts),...Object.keys(live),...Object.keys(reference)])){
      const row={label:contracts[key]?.label||key,live:live[key],capture:history[key],contract:contracts[key],reference:reference[key],change:changes[key]};
      if(this.filter==="live"&&row.live?.status!=="supported")continue;
      if(this.filter==="readonly"&&row.live?.write_access==="profile_verified")continue;
      if(this.filter==="changes"&&!row.change)continue;
      if(this.filter==="object"&&!selected.has(key))continue;
      if(!(key+" "+row.label+" "+(row.capture?.reference_index??row.reference?.reference_index??"")).toLowerCase().includes((this.query||"").toLowerCase()))continue;
      rows.push([key,row]);
    }
    return rows;
  }
  renderInventory(){
    const host=this.shadowRoot.querySelector("#inventory");
    if(!host)return;
    const entries=this.inventoryEntries(), size=50;
    const pages=Math.max(1,Math.ceil(entries.length/size));
    this.page=Math.min(this.page||0,pages-1);
    const visible=entries.slice(this.page*size,(this.page+1)*size);
    host.innerHTML=entries.length?'<div class="toolbar"><span>'+entries.length+' · '+(this.page+1)+' / '+pages+'</span><button data-page="-1" '+(!this.page?"disabled":"")+'>'+escape(this.t("back"))+'</button><button data-page="1" '+(this.page+1>=pages?"disabled":"")+'>'+escape(this.t("next"))+'</button></div><table><thead><tr><th>'+escape(this.t("inventory"))+'</th><th>'+escape(this.t("status"))+'</th><th>'+escape(this.t("details"))+'</th></tr></thead><tbody>'+visible.map(([key,row])=>'<tr><td>'+escape(row.label)+'<br><small>'+escape(key)+'</small></td><td>'+escape(row.live?.status||row.capture?.status||row.reference?.status||this.t("not_confirmed"))+'<br><small>'+escape(row.live?.write_access||row.capture?.write_access_reason||row.contract?.catalogue_binding||"")+'</small></td><td><details><summary>'+escape(this.t("details"))+'</summary><pre>'+escape(JSON.stringify(row,null,2))+'</pre></details></td></tr>').join("")+'</tbody></table>':'<p>'+escape(this.t("none"))+'</p>';
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
    return '<section class="card"><h2>'+escape(this.capabilityLabel(row))+'</h2><p>'+escape(this.t("confirm_schedule"))+'</p>'+editor.windows.map((pair,i)=>'<div class="interval"><label>'+escape(this.t("start"))+' <input type="time" step="900" data-window="'+i+'" data-edge="0" value="'+escape(pair[0])+'"></label><label>'+escape(this.t("end"))+' <input type="time" step="900" data-window="'+i+'" data-edge="1" value="'+escape(pair[1])+'"></label><button data-remove="'+i+'">'+escape(this.t("remove"))+'</button></div>').join("")+'<p>'+escape(this.t("shared"))+': '+escape((editor.review?.references||row.shared_references||[]).join(", ")||this.t("no_refs"))+'</p>'+(editor.review?'<p class="value">'+escape(editor.review.value)+'</p>':"")+'<div class="actions"><button data-add '+(editor.windows.length>=6?"disabled":"")+'>'+escape(this.t("add"))+'</button><button data-preview '+(this.pending?"disabled":"")+'>'+escape(this.t("preview"))+'</button><button data-save class="primary" '+(!editor.review||this.pending?"disabled":"")+'>'+escape(this.t("apply"))+'</button><button data-cancel>'+escape(this.t("cancel"))+'</button></div></section>';
  }
  async action(row,value,preview=false){
    if(!row||!this.allowed(row))return;
    this.pending=true;
    this.notice=this.t("working");
    this.render();
    try{
      const review=row.schedule&&!preview?this.editor?.review:null;
      const result=await this._hass.callWS({type:"deltasol/dashboard/action",entry_id:this.context,entity_id:row.entity_id,value,preview,...(review?{expected:review.expected,references:review.references,reference_token:review.reference_token}:{})});
      if(preview&&this.editor){this.editor.review=result;this.notice="";}
      else{this.notice=this.t(result.outcome)+(result.value==null?"":": "+result.value);this.editor=null;}
    }catch{this.notice=this.t("error");if(this.editor)this.editor.review=null;}
    finally{this.pending=false;this.render();}
  }
  async openHistory(row,days=1){
    if(!row)return;
    const history={id:row.entity_id,name:this.capabilityLabel(row),items:null,days,unit:this.state(row)?.attributes?.unit_of_measurement||"",error:false};
    this.history=history;
    this.render();
    try{
      const end=new Date(), start=new Date(end.getTime()-days*86400000);
      const result=await this._hass.callWS({type:"history/history_during_period",start_time:start.toISOString(),end_time:end.toISOString(),entity_ids:[row.entity_id],minimal_response:true,no_attributes:true,significant_changes_only:false});
      if(this.history===history)history.items=result[row.entity_id]||[];
    }catch{if(this.history===history){history.error=true;history.items=[];}}
    this.render();
  }
  historyPoints(items){
    return items.map(item=>{
      const stamp=item.lu??item.last_updated??item.last_changed;
      const time=typeof stamp==="number"?stamp*1000:Date.parse(stamp);
      const raw=item.s??item.state;
      const value=raw==null||String(raw).trim()===""?NaN:Number(raw);
      return {time,value,raw};
    }).filter(point=>Number.isFinite(point.time)).sort((a,b)=>a.time-b.time);
  }
  historyChart(items,unit){
    const points=this.historyPoints(items);
    const observed=points.filter(point=>Number.isFinite(point.value));
    if(!observed.length)return "";
    const first=points[0].time,last=points[points.length-1].time;
    const min=observed.reduce((a,p)=>Math.min(a,p.value),Infinity),max=observed.reduce((a,p)=>Math.max(a,p.value),-Infinity);
    const x=p=>48+(p.time-first)/Math.max(1,last-first)*540;
    const y=p=>18+(max-p.value)/Math.max(max-min,0.000001)*142;
    let segments=[],current=[];
    for(const p of points){
      if(!Number.isFinite(p.value)){if(current.length)segments.push(current);current=[];continue;}
      current.push(p);
    }
    if(current.length)segments.push(current);
    const lines=segments.map(segment=>'<polyline fill="none" stroke="var(--primary-color)" stroke-width="2" points="'+segment.map(p=>x(p).toFixed(2)+","+y(p).toFixed(2)).join(" ")+'"/>'+(segment.length===1?'<circle cx="'+x(segment[0]).toFixed(2)+'" cy="'+y(segment[0]).toFixed(2)+'" r="3" fill="var(--primary-color)"/>':"")).join("");
    return '<svg role="img" aria-label="'+escape(this.t("recorded_values")+" "+unit)+'" viewBox="0 0 610 190" width="100%" style="max-height:280px"><line x1="48" x2="588" y1="18" y2="18" stroke="var(--divider-color)"/><line x1="48" x2="588" y1="160" y2="160" stroke="var(--divider-color)"/><text x="44" y="22" text-anchor="end" fill="currentColor" font-size="11">'+escape(String(max))+'</text><text x="44" y="164" text-anchor="end" fill="currentColor" font-size="11">'+escape(String(min))+'</text>'+lines+'<text x="48" y="180" font-size="10" fill="currentColor">'+escape(new Date(first).toLocaleDateString(this._hass?.language))+'</text><text x="588" y="180" text-anchor="end" font-size="10" fill="currentColor">'+escape(new Date(last).toLocaleDateString(this._hass?.language))+'</text></svg>';
  }
  historyView(){
    const h=this.history;
    return '<section class="card"><div class="toolbar"><h2 class="grow">'+escape(this.t("history")+' — '+h.name)+'</h2><select id="history-range" aria-label="'+escape(this.t("range"))+'">'+[[1,"day"],[7,"week"],[30,"month"]].map(([days,key])=>'<option value="'+days+'" '+(h.days===days?"selected":"")+'>'+escape(this.t(key))+'</option>').join("")+'</select><button data-close-history>'+escape(this.t("close"))+'</button></div>'+(h.items===null?'<p>'+escape(this.t("loading"))+'</p>':h.error?'<p class="warning">'+escape(this.t("history_error"))+'</p>':h.items.length?this.historyChart(h.items,h.unit)+'<details><summary>'+escape(this.t("recorded_values"))+'</summary><div class="technical"><table><tbody>'+h.items.slice(-200).reverse().map(item=>{const ts=item.lu??item.last_updated??item.last_changed;const date=typeof ts==="number"?new Date(ts*1000):new Date(ts);return '<tr><td>'+escape(Number.isNaN(date.getTime())?"":date.toLocaleString(this._hass?.language))+'</td><td>'+escape(item.s??item.state)+'</td></tr>';}).join("")+'</tbody></table></div></details>':'<p>'+escape(this.t("no_history"))+'</p>')+'</section>';
  }
  change(event){
    const target=event.target;
    if(this.pending){this.render();return;}
    if(target.id==="installation"){this.context=target.value;this.object="";this.editor=null;this.history=null;this.report=null;if(!this.tabs().includes(this.tab))this.tab="overview";this.render();if(this.tab==="engineering")this.engineering();}
    if(target.id==="object"){this.object=target.value;this.page=0;this.render();}
    if(target.id==="technical-filter"){this.filter=target.value;this.page=0;this.renderInventory();}
    if(target.id==="history-range"&&this.history)this.openHistory(this.row(this.history.id),Number(target.value));
    if(target.dataset.window!==undefined&&this.editor){this.editor.windows[Number(target.dataset.window)][Number(target.dataset.edge)]=target.value;this.editor.review=null;this.render();}
  }
  click(event){
    const button=event.target.closest("button");if(!button||button.disabled||this.pending)return;
    const d=button.dataset;
    if(d.refresh!==undefined){this.load();if(this.tab==="engineering")this.engineering();}
    else if(d.tab){this.tab=d.tab;this.editor=null;this.history=null;this.notice="";this.render();if(this.tab==="engineering")this.engineering();}
    else if(d.sourceObject){this.object=d.sourceObject;this.tab="overview";this.editor=null;this.history=null;this.render();}
    else if(d.object){this.object=d.object;this.render();}
    else if(d.history){this.openHistory(this.row(d.history));}
    else if(d.closeHistory!==undefined){this.history=null;this.render();}
    else if(d.edit){this.edit(this.row(d.edit));}
    else if(d.action){this.action(this.row(d.action),JSON.parse(d.value));}
    else if(d.apply){const input=Array.from(this.shadowRoot.querySelectorAll("[data-input]")).find(node=>node.dataset.input===d.apply);const row=this.row(d.apply);if(input&&row&&input.value!==""&&input.reportValidity())this.action(row,row.kind==="number"?Number(input.value):input.value);}
    else if(d.add!==undefined&&this.editor){this.editor.windows.push(["08:00","09:00"]);this.editor.review=null;this.render();}
    else if(d.remove!==undefined&&this.editor){this.editor.windows.splice(Number(d.remove),1);this.editor.review=null;this.render();}
    else if(d.cancel!==undefined){this.editor=null;this.render();}
    else if(d.preview!==undefined&&this.editor){this.action(this.row(this.editor.id),this.editor.windows.map(pair=>pair.join("-")).join(", "),true);}
    else if(d.save!==undefined&&this.editor?.review){this.action(this.row(this.editor.id),this.editor.review.value);}
    else if(d.reportRefresh!==undefined){this.engineering();}
    else if(d.page!==undefined){this.page+=Number(d.page);this.renderInventory();}
    else if(d.export!==undefined&&this.report){const url=URL.createObjectURL(new Blob([JSON.stringify(this.report,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download="resol-diagnostics.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),0);}
  }
}
if(!customElements.get("deltasol-dashboard"))customElements.define("deltasol-dashboard",ResolDashboard);

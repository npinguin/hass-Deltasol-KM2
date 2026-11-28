import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";

let Panel;
const scope = {
  HTMLElement: class {},
  customElements: {get:()=>undefined,define:(_name,cls)=>{Panel=cls;}},
};
vm.runInNewContext(readFileSync(new URL("../custom_components/deltasol/frontend/dashboard.js",import.meta.url),"utf8"),scope);

function model(admin=false,engineering=false){
  const panel=Object.create(Panel.prototype);
  panel.context="one";panel.tab="overview";panel.pending=false;
  panel.installations=[{id:"one",settings:admin,engineering,objects:[{id:"device",capabilities:[
    {entity_id:"sensor.temperature",depth:"status",section:"overview",kind:"sensor"},
    {entity_id:"select.heating",depth:"controls",section:"heating",kind:"select",control:true},
    {entity_id:"text.week",depth:"settings",section:"schedule",kind:"text",control:admin,schedule:true},
    {entity_id:"button.discovery",depth:"engineering",section:"overview",kind:"button",control:engineering}
  ]}]}];
  panel._hass={language:"nl",states:{"sensor.temperature":{state:"42",attributes:{unit_of_measurement:"°C"}},"select.heating":{state:"Auto",attributes:{}},"text.week":{state:"08:00-10:00",attributes:{}},"button.discovery":{state:"unknown",attributes:{}}}};
  return panel;
}
test("single navigation, object context, role and unavailable state",()=>{
  const p=model();
  assert.ok(p.tabs().includes("heating"));
  assert.ok(!p.tabs().includes("engineering"));
  assert.ok(!p.tabs().includes("settings"));
  assert.equal(p.value(p.row("sensor.temperature")),"42 °C");
  assert.equal(p.visibleRows(p.current().objects[0]).length,2);
  p._hass.states["select.heating"].state="unavailable";
  assert.equal(p.allowed(p.row("select.heating")),false);
  assert.equal(p.value(p.row("select.heating")),"Niet beschikbaar");
});
test("administrator Settings and optional Engineering share same object",()=>{
  const p=model(true,true);
  assert.ok(p.tabs().includes("settings"));
  assert.ok(p.tabs().includes("engineering"));
  p.tab="settings";
  assert.equal(p.visibleRows(p.current().objects[0]).length,2);
  p.tab="engineering";
  assert.equal(p.visibleRows(p.current().objects[0]).length,1);
  assert.equal(p.allowed(p.row("button.discovery")),true);
});
test("weekly preview carries authoritative baseline to native action",async()=>{
  const p=model(true,true);
  p.editor={id:"text.week",windows:[["08:00","10:00"]],review:null};
  p.render=()=>{};
  let request;
  p._hass.callWS=async msg=>{
    request=msg;
    return msg.preview?{value:"08:00-10:00",expected:"07:00-09:00",references:["Heating"],reference_token:"verified-owners"}:{outcome:"observed",value:msg.value};
  };
  await p.action(p.row("text.week"),"08:00-10:00",true);
  assert.equal(p.editor.review.expected,"07:00-09:00");
  await p.action(p.row("text.week"),p.editor.review.value);
  assert.equal(request.expected,"07:00-09:00");
  assert.equal(request.references[0],"Heating");
  assert.equal(request.reference_token,"verified-owners");
  assert.equal(request.entry_id,"one");
  assert.equal(p.editor,null);
});

test("settings-only native function remains visible in the object overview",()=>{
  const p=model(true,false);
  const object={id:"thermostat",capabilities:[{entity_id:"number.thermostat",depth:"settings",section:"overview",kind:"number",control:true}]};
  assert.equal(p.visibleRows(object).length,1);
});

test("engineering inventory merges live and historical evidence without claiming write access",()=>{
  const p=model(true,true);
  p.query="";p.filter="all";p.report={configuration_discovery:{parameters:{Old:{status:"stale"}},comparison:{changes:{Old:{before_raw:1,after_raw:2}}}},configuration_controls:{Live:{status:"supported",write_access:"profile_verified"}},operational_contracts:{Live:{label:"Heating target"},Other:{label:"Unverified"}}};
  assert.equal(p.inventoryEntries().length,3);
  p.filter="live";assert.equal(p.inventoryEntries()[0][0],"Live");
  p.filter="changes";assert.equal(p.inventoryEntries()[0][0],"Old");
  p.filter="all";p.query="heating";assert.equal(p.inventoryEntries()[0][0],"Live");
});

test("reference inventory remains searchable without a historical capture",()=>{
  const p=model(true,true);
  p.filter="all";p.query="14014";p.report={reference_inventory:{HeatingMode:{reference_index:14014,status:"reference_only",write_access:"not_confirmed"}}};
  assert.equal(p.inventoryEntries()[0][0],"HeatingMode");
  assert.equal(p.inventoryEntries()[0][1].reference.write_access,"not_confirmed");
  p.filter="live";assert.equal(p.inventoryEntries().length,0);
});

test("native labels retain custom names and localize known capability semantics",()=>{
  const p=model(true,true);
  assert.equal(p.capabilityLabel({name:"Heating circuit 1 configured mode"}),"Verwarmingscircuit 1 · Bedrijfsmodus");
  assert.equal(p.capabilityLabel({name:"Hoxter target",renamed:true,native_label:"Heating circuit 1 configured mode"}),"Hoxter target");
  p._hass.language="fr";
  assert.equal(p.capabilityLabel({name:"Heating circuit 1 heating curve"}),"Circuit de chauffage 1 · Courbe de chauffe");
});

test("weekly programs use native object and program identity, not duplicate schedules",()=>{
  const p=model(true,true);
  const asset=p.current().objects[0];
  const row=p.row("text.week");row.program=1;row.day="Mo";row.shared_references=["Heating"];
  asset.capabilities.push({...row,entity_id:"text.tuesday",day:"Di"});
  assert.equal(p.weekPrograms().length,1);
  assert.equal(p.weekPrograms()[0].days.size,2);
  assert.equal(p.capabilityLabel(row),"Weekprogramma 1 · Maandag");
  assert.ok(p.weekView().includes("08:00-10:00"));
  p.object="other";assert.equal(p.weekPrograms().length,0);
});

test("semantic summary prioritizes real mode and measurements over registry order",()=>{
  const p=model(true,true);
  const rows=[{name:"Preference",depth:"settings",priority:10},{name:"Temperature",depth:"status",priority:95},{name:"Mode",depth:"controls",priority:100},{name:"Power",depth:"status",priority:90},{name:"Alarm",depth:"status",priority:120},{name:"Flow",depth:"status",priority:75}];
  assert.equal(p.prominentRows(rows).map(row=>row.name).join(","),"Alarm,Mode,Temperature,Power");
});
test("function detail follows a positively observed shared program without duplicating it",()=>{
  const p=model(true,true);
  const asset=p.current().objects[0], row=p.row("text.week");
  row.program=3;row.day="Mo";
  asset.capabilities=asset.capabilities.filter(item=>item!==row);
  asset.capabilities[0].linked_program=3;
  p.current().objects.push({id:"program-owner",capabilities:[row]});
  p.object=asset.id;
  assert.equal(p.weekPrograms().length,1);
  assert.equal(p.weekPrograms()[0].number,3);
  assert.ok(!p.weekView(false).includes("data-edit"));
  assert.ok(p.weekView(true).includes("data-edit"));
  asset.capabilities[0].linked_program=null;
  assert.equal(p.weekPrograms().length,0);
});

test("cached values are not presented as current when native availability is lost",()=>{
  const p=model(true,true),row=p.row("sensor.temperature");
  row.availability="telemetry_unavailable";
  assert.equal(p.valid(row),false);
  assert.equal(p.value(row),"Niet beschikbaar");
  row.availability="available";assert.equal(p.value(row),"42 °C");
});
test("recorded numeric trends preserve gaps and do not invent readings",()=>{
  const p=model(true,true),items=[{lu:1,s:"42"},{lu:2,s:"unavailable"},{lu:3,s:"44"},{lu:4,s:"45"}];
  assert.equal(p.historyPoints(items).filter(x=>Number.isFinite(x.value)).length,3);
  const chart=p.historyChart(items,"°C");
  assert.equal((chart.match(/<polyline/g)||[]).length,2);
  assert.equal(p.historyChart([{lu:1,s:"Auto"},{lu:2,s:"Off"}],""),"");
});

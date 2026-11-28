import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {mkdtempSync,writeFileSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join,resolve} from "node:path";
import {pathToFileURL} from "node:url";
import test from "node:test";

function journey(){
  const panel=document.querySelector("deltasol-dashboard"), calls=[];
  const check=(value,message)=>{if(!value)throw new Error(message);};
  const tick=async()=>{for(let i=0;i<6;i++)await Promise.resolve();};
  const state=(state,attributes={})=>({state,attributes});
  const rows=[
    {entity_id:"sensor.temp",name:"Buffer temperature",section:"overview",depth:"status",kind:"sensor"},
    {entity_id:"select.mode",name:"Heating mode",section:"heating",depth:"controls",kind:"select",control:true},
    {entity_id:"text.week",name:"Monday",section:"schedule",depth:"settings",kind:"text",control:true,schedule:true,shared_references:["Heating"]},
    {entity_id:"button.discovery",name:"Discovery",section:"overview",depth:"engineering",kind:"button",control:true}
  ];
  let admin=true,engineering=true;
  const hass={language:"en",states:{"sensor.temp":state("42",{unit_of_measurement:"°C"}),"select.mode":state("Auto",{options:["Auto","Off"]}),"text.week":state("08:00-10:00"),"button.discovery":state("unknown")},callWS:async request=>{
    calls.push(request);
    if(request.type.endsWith("/list"))return [{id:"one",name:"System 1",settings:admin,engineering:admin&&engineering,health:{telemetry:true,parameter_access:true,catalogue:true,selectors_observed:12,selectors_total:16,checkpoint:"checkpoint_incompatible"},objects:[{id:"native",name:"RESOL Arrangement function 1",object_kind:"solid_fuel",object_number:1,capabilities:rows.filter(row=>admin||row.depth!=="engineering"&&row.depth!=="settings")}]},{id:"two",name:"System 2",settings:admin,engineering:false,objects:[]}];
    if(request.type.endsWith("/engineering"))return {configuration_discovery:{parameters:{Schema:{status:"read"}}}};
    if(request.type.endsWith("/action"))return request.preview?{value:request.value,expected:"08:00-10:00",references:["Heating"],reference_token:"known-links"}:{outcome:"observed",value:request.value};
    throw new Error("Unexpected request "+request.type);
  }};
  const root=panel.shadowRoot;
  const click=selector=>{const element=root.querySelector(selector);check(element,"Missing "+selector);element.click();};
  const choose=(selector,value)=>{const element=root.querySelector(selector);element.value=value;element.dispatchEvent(new Event("change",{bubbles:true}));};
  async function run(){
    panel.hass=hass;await tick();
    check(root.textContent.includes("Solid-fuel heating"),"No native function object");
    check(root.textContent.includes("Historical discovery needs attention"),"Missing checkpoint warning");
    check(root.textContent.includes("12 / 16"),"Missing live discovery coverage");
    click('[data-tab="heating"]');
    check(root.textContent.includes("Heating mode"),"Missing routine control");
    choose('[data-input="select.mode"]',"Off");
    click('[data-apply="select.mode"]');await tick();
    check(calls.some(x=>x.entity_id==="select.mode"&&x.value==="Off"),"Missing native action");
    click('[data-tab="settings"]');click('[data-edit="text.week"]');
    click("[data-preview]");await tick();
    check(root.textContent.includes("Heating"),"Shared schedule references missing");
    click("[data-save]");await tick();
    check(calls.some(x=>x.entity_id==="text.week"&&!x.preview&&x.expected==="08:00-10:00"&&x.reference_token==="known-links"),"Missing reviewed schedule save");
    click('[data-tab="engineering"]');await tick();
    check(root.textContent.includes("Schema"),"Missing native parameter inventory");
    choose("#installation","two");
    check(!root.querySelector('[data-tab="engineering"]'),"Engineering not scoped");
    admin=false;engineering=false;
    root.activeElement?.blur();await panel.load();choose("#installation","one");
    check(!root.querySelector('[data-tab="settings"]')&&!root.querySelector('[data-tab="engineering"]'),"Role tabs leaked");
    check(root.querySelector("main").scrollWidth<=innerWidth+1,"Horizontal overflow");
    document.body.dataset.result="PASS";
  }
  run().catch(error=>{document.body.dataset.result="FAIL";document.body.append(Object.assign(document.createElement("pre"),{textContent:error.stack}));});
}
const script=pathToFileURL(resolve("custom_components/deltasol/frontend/dashboard.js")).href;
for(const width of [390,768,1280])for(const theme of ["light","dark"]){
  test("RESOL Chromium "+width+" "+theme,()=>{
    const dir=mkdtempSync(join(tmpdir(),"resol-browser-"));
    try{
      const file=join(dir,"fixture.html");
      const colors=theme==="light"?"--primary-text-color:#15212a;--primary-background-color:#f4f5f6;--card-background-color:white;--secondary-text-color:#53616b;--divider-color:#ccd2d7":"--primary-text-color:white;--primary-background-color:#182129;--card-background-color:#26313b;--secondary-text-color:#b9c2cc;--divider-color:#46535e";
      writeFileSync(file,'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;font:16px sans-serif}body{width:'+width+'px;max-width:100vw;'+colors+';--primary-color:#3975a8;--text-primary-color:white}</style><body data-result="PENDING"><deltasol-dashboard></deltasol-dashboard><script src="'+script+'"></script><script>('+journey.toString()+')()</script></body>');
      const result=spawnSync(process.env.RESOL_BROWSER||"google-chrome",["--headless","--no-sandbox","--disable-dev-shm-usage","--disable-background-networking","--no-first-run","--window-size="+width+",900","--virtual-time-budget=3000","--dump-dom",pathToFileURL(file).href],{encoding:"utf8",timeout:30000});
      assert.ifError(result.error);assert.equal(result.status,0,result.stderr);
      assert.equal(result.stdout.match(/<body[^>]*data-result="([^"]+)"/)?.[1],"PASS",result.stdout.match(/<pre>[\s\S]*?<\/pre>/)?.[0]||"Browser journey incomplete");
    }finally{rmSync(dir,{recursive:true,force:true});}
  });
}

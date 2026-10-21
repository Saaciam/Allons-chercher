"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const content = $("#page-content");
const dialog = $("#modal");
const modalBody = $("#modal-body");
const pageNames = {dashboard: "Tableau de bord", offers: "Offres", alerts: "Mes alertes", applications: "Candidatures", contacts: "Contacts", profile: "Mon profil"};
let state = null;
let currentPage = "dashboard";
let searchWarnings = [];
let searchMessage = "";
let searchResults = null;
let searchSourceStatus = [];
let offersView = "saved";
let settings = null;
let lastCriteria = null;
let locationResults = new Map();
let loadSequence = 0;

const iconPaths = {
  search: ["circle", {cx:10.5,cy:10.5,r:6.5}, "path", {d:"m16 16 5 5"}],
  pin: ["path", {d:"M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0Z"}, "circle", {cx:12,cy:10,r:2.5}],
  briefcase: ["rect", {x:3,y:7,width:18,height:14,rx:2}, "path", {d:"M8 7V4h8v3M3 12h18M10 12v3h4v-3"}],
  bell: ["path", {d:"M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"}],
  document: ["path", {d:"M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h7"}],
  arrow: ["path", {d:"M5 12h14m-5-5 5 5-5 5"}],
  plus: ["path", {d:"M12 5v14M5 12h14"}],
  check: ["path", {d:"m5 12 4 4L19 6"}],
  mail: ["rect", {x:3,y:5,width:18,height:14,rx:2}, "path", {d:"m3 6 9 7 9-7"}],
  users: ["circle", {cx:9,cy:8,r:3}, "path", {d:"M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6M18 15a5 5 0 0 1 3 5"}],
  clock: ["circle", {cx:12,cy:12,r:9}, "path", {d:"M12 7v5l3 2"}],
  target: ["circle", {cx:12,cy:12,r:6}, "circle", {cx:12,cy:12,r:2}, "path", {d:"M12 2v4M12 18v4M2 12h4M18 12h4"}],
  edit: ["path", {d:"m15 4 5 5M4 20l4-1 12-12a3 3 0 0 0-4-4L4 15z"}],
  trash: ["path", {d:"M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7M14 10v7"}],
  refresh: ["path", {d:"M20 7v5h-5M4 17v-5h5M5 7a8 8 0 0 1 13-2l2 3M4 16l2 3a8 8 0 0 0 13-2"}],
  external: ["path", {d:"M14 3h7v7M10 14 21 3M21 14v7H3V3h7"}],
  upload: ["path", {d:"M12 16V3m-5 5 5-5 5 5M3 16v5h18v-5"}],
  phone: ["path", {d:"M7 3H3v4c0 8 6 14 14 14h4v-4l-5-2-2 3a15 15 0 0 1-8-8l3-2z"}],
  link: ["path", {d:"m10 14 4-4M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0M16 8l2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0"}],
  save: ["path", {d:"M4 3h14l3 3v15H3V3zM7 3v6h10V3M7 21v-8h10v8"}],
};

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true");
  const parts = iconPaths[name] || iconPaths.document;
  for (let i = 0; i < parts.length; i += 2) {
    const item = document.createElementNS("http://www.w3.org/2000/svg", parts[i]);
    for (const [key, value] of Object.entries(parts[i + 1])) item.setAttribute(key, String(value));
    svg.append(item);
  }
  return svg;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "checked" || key === "disabled" || key === "hidden" || key === "required") node[key] = Boolean(value);
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}

function button(label, action, variant = "", iconName = null, attrs = {}) {
  return el("button", {type:"button", class:`button ${variant}`, onclick:action, ...attrs}, iconName ? icon(iconName) : null, label);
}
function textLink(label, action, iconName = "arrow") { return el("button", {type:"button",class:"text-link",onclick:action}, label, icon(iconName)); }
function tag(label, variant = "") { return el("span", {class:`tag ${variant}`}, label); }
function initials(value = "") { return String(value).split(/[\s-]+/).filter(Boolean).slice(0,2).map(word => word[0]).join("").toUpperCase() || "–"; }
function monogram(company) { return el("span", {class:"company-monogram", "aria-hidden":"true"}, initials(company)); }
function shortDate(value, includeTime = false) {
  if (!value) return "À vérifier";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "À vérifier";
  return new Intl.DateTimeFormat("fr-FR", {day:"numeric",month:"short",...(includeTime ? {hour:"2-digit",minute:"2-digit"} : {}), timeZone:"Europe/Paris"}).format(date);
}
function statusTag(status) { return status === "sent" || status === "envoyee" ? tag("Envoyée", "green") : status === "failed" ? tag("À vérifier", "amber") : tag("Prête à relire", "blue"); }
function field(label, input, help = "") { return el("label", {class:"field"}, el("span", {}, label), input, help ? el("small", {class:"form-help"}, help) : null); }
function input(name, value = "", attrs = {}) { return el("input", {name, value:value ?? "", ...attrs}); }
function select(name, options, selected) {
  const node = el("select", {name});
  for (const item of options) { const pair = Array.isArray(item) ? item : [item, item]; const option = el("option", {value:pair[0]}, pair[1]); option.selected = String(pair[0]) === String(selected); node.append(option); }
  return node;
}
function errorBox() { return el("div", {class:"form-error",role:"alert"}); }
function infoBox(message, variant = "") { return el("div", {class:`info-box ${variant}`}, message); }
function heading(title, subtitle, actions = []) { return el("div", {class:"page-heading"}, el("div", {}, el("h1", {}, title), el("p", {class:"subtitle"}, subtitle)), actions.length ? el("div", {class:"heading-actions"}, actions) : null); }
function emptyState(title, description, action = null, wide = false, iconName = "briefcase") { return el("div", {class:`empty-state ${wide ? "wide-empty" : ""}`}, el("div", {class:"empty-icon"}, icon(iconName)), el("h3", {}, title), el("p", {}, description), action); }
function card(title, body, action = null) { return el("section", {class:"card"}, el("div", {class:"card-header"}, el("h2", {}, title), action), body); }
function safeUrl(value) {
  try { const url = new URL(value, window.location.origin); return ["http:", "https:"].includes(url.protocol) ? url.href : null; } catch { return null; }
}
function externalLink(label, url, className = "text-link") { const safe = safeUrl(url); return safe ? el("a", {href:safe,target:"_blank",rel:"noopener noreferrer",class:className}, label, icon("external")) : null; }
function toast(message, variant = "") {
  const node = el("div", {class:`toast ${variant}`}, message);
  $("#toast-region").append(node);
  window.setTimeout(() => node.remove(), variant === "error" ? 9000 : 5000);
}
async function api(path, data, method = data === undefined ? "GET" : "POST") {
  const headers = {"Accept":"application/json"};
  if (data !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = state?.csrf_token || "";
  const response = await fetch(path, {method,headers,credentials:"same-origin",...(data !== undefined ? {body:JSON.stringify(data)} : {})});
  let result;
  try { result = await response.json(); } catch { throw new Error("Le serveur n’a pas renvoyé de réponse lisible. Relancez votre espace et réessayez."); }
  if (!response.ok) {
    const error = new Error(typeof result.error === "string" ? result.error : result.message || "Cette action n’a pas abouti. Réessayez.");
    error.status = response.status;
    for (const key of ["code","application_id","can_regenerate"]) if (result[key] !== undefined) error[key] = result[key];
    throw error;
  }
  return result;
}
async function busy(node, task, errorTarget = null) {
  if (node?.disabled) return;
  if (node) { node.disabled = true; node.setAttribute("aria-busy", "true"); }
  if (errorTarget) errorTarget.textContent = "";
  try { return await task(); } catch (error) { if (errorTarget) errorTarget.textContent = error.message; else toast(error.message, "error"); }
  finally { if (node) { node.disabled = false; node.removeAttribute("aria-busy"); } }
}
function openModal(title, body, wide = false, kicker = "MON ESPACE") {
  $("#modal-title").textContent = title; $("#modal-kicker").textContent = kicker;
  modalBody.replaceChildren(body); dialog.classList.toggle("wide-modal", wide);
  if (!dialog.open) dialog.showModal();
}
function closeModal() { dialog.close(); }
function navigate(page) { window.location.hash = page; if (currentPage === page) render(); }
async function loadState(renderPage = true) {
  const sequence = ++loadSequence;
  const result = await api("/api/state");
  if (sequence !== loadSequence) return;
  state = result;
  for (const key of ["alerts","offers","applications","contacts","notifications"]) if (!Array.isArray(state[key])) state[key] = [];
  state.preferences ||= {}; state.integrations ||= {};
  if(searchResults === null && Array.isArray(state.last_search?.offer_ids)) {
    searchResults=state.last_search.offer_ids.map(id=>state.offers.find(offer=>offer.id === id)).filter(Boolean);
    searchWarnings=state.last_search.warnings || [];
    searchSourceStatus=state.last_search.source_status || [];
    lastCriteria ||= state.last_search.criteria || null;
    offersView="search";
    searchMessage=`${searchResults.length} offre(s) trouvée(s) pour votre recherche.`;
  }
  updateChrome(); if (renderPage) render();
  return result;
}
function updateChrome() {
  const identity = state.profile?.identity || {};
  const name = identity.first_name?.split(" ")[0] || identity.display_name?.split(" ")[0] || "Mon profil";
  $("#sidebar-name").textContent = name;
  const avatar = initials(identity.display_name || [identity.first_name,identity.last_name].filter(Boolean).join(" "));
  $("#sidebar-avatar").textContent = avatar; $("#topbar-avatar").textContent = avatar;
  $("#offers-count").textContent = state.offers.length; $("#offers-count").hidden = state.offers.length === 0;
  $("#applications-count").textContent = state.applications.length;
  const unread = state.notifications.filter(item => !item.read).length;
  $("#notification-count").textContent = unread; $("#notification-count").hidden = unread === 0;
}
function render() {
  currentPage = pageNames[window.location.hash.slice(1)] ? window.location.hash.slice(1) : "dashboard";
  $("#page-title").textContent = pageNames[currentPage];
  document.title = `Mon stage · ${pageNames[currentPage]}`;
  document.querySelectorAll("[data-page]").forEach(node => { const active = node.dataset.page === currentPage; node.classList.toggle("active",active); if (active) node.setAttribute("aria-current","page"); else node.removeAttribute("aria-current"); });
  if (!state) return;
  const pages = {dashboard:renderDashboard,offers:renderOffers,alerts:renderAlerts,applications:renderApplications,contacts:renderContacts,profile:renderProfile};
  content.replaceChildren(pages[currentPage]());
}

function searchForm() {
  const prefs = {...(state.preferences || {}),...(settings?.defaults || {}),...(state.search_defaults || {}),...(lastCriteria || {})};
  const keywords = input("keywords", prefs.keywords || "Data, intelligence artificielle, Business Intelligence", {placeholder:"Un métier, des compétences…",required:true,maxlength:180});
  const location = input("location", prefs.location || "", {placeholder:"Ville ou partout en France",list:"location-suggestions",autocomplete:"off",maxlength:180});
  const geolocate = el("button", {type:"button",class:"icon-button",title:"Utiliser ma position", "aria-label":"Utiliser ma position"}, icon("target"));
  geolocate.addEventListener("click", () => useGeolocation(location,geolocate));
  wireLocation(location);
  const radius = select("radius_km",[[10,"À 10 km"],[30,"À 30 km"],[50,"À 50 km"],[100,"À 100 km"],[200,"À 200 km"]],prefs.radius_km || 30); radius.className = "inline-select"; radius.setAttribute("aria-label","Rayon de recherche");
  const contract = select("contract",[["stage","Stage"],["alternance","Alternance"],["cdi","CDI"],["cdd","CDD"],["all","Tous les contrats"]],prefs.contract || "stage"); contract.className = "inline-select"; contract.setAttribute("aria-label","Type de contrat");
  const remote = input("remote", "", {type:"checkbox",checked:prefs.remote});
  const source = sourceSelect(prefs.source || "all"); source.className="inline-select"; source.setAttribute("aria-label","Source d’offres");
  const error = errorBox();
  const submit = button("Rechercher", null, "primary", "search", {type:"submit"});
  const form = el("form", {class:"search-form"}, field("QUEL STAGE VOUS INTÉRESSE ?",el("div",{class:"input-shell"},icon("search"),keywords)), field("OÙ SOUHAITEZ-VOUS TRAVAILLER ?",el("div",{class:"input-shell"},icon("pin"),location,geolocate)), submit);
  const getCriteria = () => ({keywords:keywords.value.trim(),...locationCriteria(location),radius_km:Number(radius.value),contract:contract.value,remote:remote.checked,source:source.value});
  form.addEventListener("submit", event => {
    event.preventDefault();
    busy(submit, async () => {
      await resolveTypedLocation(location);
      lastCriteria = getCriteria();
      await api("/api/preferences",lastCriteria);
      const result = await api("/api/search",lastCriteria);
      searchWarnings = result.warnings || [];
      searchResults = Array.isArray(result.offers) ? result.offers : [];
      searchSourceStatus = Array.isArray(result.source_status) ? result.source_status : [];
      offersView = "search";
      searchMessage = `${searchResults.length} offre(s) trouvée(s) pour votre recherche.`;
      await loadState(false); navigate("offers");
      if (result.offers?.length) toast(searchMessage);
    },error);
  });
  const create = textLink("Créer une alerte avec cette recherche",()=> {lastCriteria = getCriteria(); openAlertModal(null,lastCriteria);},"bell");
  return el("section", {class:"search-card"},el("div",{class:"section-header"},el("div",{},el("h2",{},"Votre prochain stage commence ici"),el("p",{},"Un métier, un lieu, et des opportunités à découvrir.")),tag("À votre rythme","green")),form,el("div",{class:"filter-row"},radius,contract,source,el("label",{class:"check-label"},remote,"Télétravail uniquement"),create),error);
}

function sourceSelect(selected = "all") {
  return select("source",[["all","Toutes les sources disponibles"],["smartrecruiters","SmartRecruiters"],["france-travail","France Travail"],["arbeitnow","Arbeitnow"]],selected);
}

function locationCriteria(inputNode) {
  const location = inputNode.value.trim();
  const picked = locationResults.get(location);
  if (picked) return {location,city_code:picked.city_code || "",latitude:picked.latitude ?? null,longitude:picked.longitude ?? null};
  if (inputNode.dataset.location === location) return {location,city_code:inputNode.dataset.cityCode || "",latitude:inputNode.dataset.latitude ? Number(inputNode.dataset.latitude) : null,longitude:inputNode.dataset.longitude ? Number(inputNode.dataset.longitude) : null};
  const saved = state.preferences || {};
  if (saved.location === location) return {location,city_code:saved.city_code || "",latitude:saved.latitude ?? null,longitude:saved.longitude ?? null};
  return {location,city_code:"",latitude:null,longitude:null};
}
function wireLocation(node) {
  let timeout, sequence = 0;
  node.addEventListener("input", () => {
    clearTimeout(timeout); const query = node.value.trim(); const current = ++sequence;
    if (query.length < 2) return;
    timeout = window.setTimeout(async () => {
      try { const result = await api(`/api/locations?q=${encodeURIComponent(query)}`); if (current !== sequence || !node.isConnected) return; const suggestions = (result.locations || []).slice(0,10); suggestions.forEach(item => locationResults.set(item.label,item)); $("#location-suggestions").replaceChildren(...suggestions.map(item => el("option",{value:item.label}))); } catch { /* A location may also be entered manually. */ }
    },350);
  });
}
async function resolveTypedLocation(node) {
  const picked = locationCriteria(node);
  if (!picked.location || picked.city_code || (picked.latitude != null && picked.longitude != null)) return picked;
  try {
    const result = await api(`/api/locations?q=${encodeURIComponent(picked.location)}`);
    const locations = result.locations || [];
    const normalize = value => String(value).normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase().trim();
    const query = normalize(picked.location);
    const match = locations.find(item => normalize(item.name || item.city || item.label?.split(/[,(]/)[0]) === query) || (locations.length === 1 ? locations[0] : null);
    if (match) {
      node.dataset.location = picked.location; node.dataset.cityCode = match.city_code || "";
      if (match.latitude != null) node.dataset.latitude = String(match.latitude);
      if (match.longitude != null) node.dataset.longitude = String(match.longitude);
      return locationCriteria(node);
    }
    toast("Sélectionnez une ville dans les suggestions pour préciser le rayon de recherche.","info");
  } catch { toast("Votre ville est enregistrée. Sélectionnez-la dans les suggestions quand la localisation sera disponible pour préciser le rayon.","info"); }
  return picked;
}
async function useGeolocation(location, node) {
  if (!navigator.geolocation) { toast("Votre navigateur ne propose pas la localisation. Saisissez votre ville.","error"); return; }
  await busy(node, async () => {
    const position = await new Promise((resolve,reject) => navigator.geolocation.getCurrentPosition(resolve,()=>reject(new Error("La localisation n’est pas disponible. Vous pouvez saisir votre ville.")),{enableHighAccuracy:false,timeout:12000,maximumAge:300000}));
    const result = await api("/api/location/reverse",{latitude:position.coords.latitude,longitude:position.coords.longitude});
    const item = typeof result.location === "string" ? {label:result.location,latitude:position.coords.latitude,longitude:position.coords.longitude} : result.location;
    if (!item?.label && !item?.location) throw new Error("Votre ville n’a pas été trouvée. Saisissez-la dans le champ.");
    const label = item.label || item.location;
    const matched = {...item,latitude:item.latitude ?? position.coords.latitude,longitude:item.longitude ?? position.coords.longitude};
    location.value = label; locationResults.set(label,matched); location.dataset.location = label; location.dataset.cityCode = item.city_code || ""; location.dataset.latitude = String(matched.latitude); location.dataset.longitude = String(matched.longitude);
    toast("Votre ville a été renseignée.");
  });
}

function renderDashboard() {
  const name = state.profile?.identity?.first_name?.split(" ")[0] || "Christopher";
  const internship = state.profile?.internship || {};
  const sent = state.applications.filter(app => ["sent","envoyee"].includes(app.status)).length;
  const active = state.alerts.filter(alert => alert.enabled).length;
  const stat = (label,value,note,iconName,positive = false) => el("div",{class:"stat-card"},el("div",{class:"stat-top"},el("span",{class:"stat-label"},label),el("span",{class:"stat-icon"},icon(iconName))),el("div",{class:"stat-value"},value),el("div",{class:`stat-note ${positive ? "positive" : ""}`},note));
  const hero = el("section",{class:"hero"},el("div",{class:"hero-content"},el("span",{class:"eyebrow"},"LE BON STAGE, LA BONNE CANDIDATURE"),el("h2",{},"Donnez une longueur d’avance",el("br"),"à votre recherche."),el("p",{class:"subtitle"},"Des offres à votre CV personnalisé, avancez étape par étape."),el("div",{class:"hero-tags"},el("span",{class:"hero-tag"},icon("briefcase"),`Stage · ${internship.duration_months || 6} mois`),el("span",{class:"hero-tag"},icon("clock"),internship.available_from ? `Dès ${new Intl.DateTimeFormat("fr-FR",{month:"long",year:"numeric",timeZone:"Europe/Paris"}).format(new Date(internship.available_from))}` : "Disponibilité à préciser"),el("span",{class:"hero-tag"},icon("pin"),internship.mobility || "Mobilité à préciser"))),el("div",{class:"hero-illustration","aria-hidden":"true"},el("div",{class:"paper-sheet"},el("div",{class:"paper-line"}),el("div",{class:"paper-line short"}),el("div",{class:"paper-line"}),el("div",{class:"paper-line short"})),el("div",{class:"floating-check"},"✓"),el("div",{class:"floating-arrow"},"↗")));
  const recentOffers = state.offers.slice(0,3);
  const offerBody = recentOffers.length ? el("div",{},recentOffers.map(offer => compactOffer(offer))) : emptyState("Vos premières opportunités vous attendent", "Lancez une recherche ou importez une offre qui vous plaît. Les résultats apparaîtront ici.",textLink("Importer une offre",()=>openOfferModal(),"plus"));
  const applications = state.applications.slice(0,4);
  const appBody = applications.length ? el("div",{},applications.map(app => el("div",{class:"activity-item"},el("div",{class:"activity-icon"},icon(app.status === "sent" ? "check" : "document")),el("div",{},el("h3",{},app.company || app.entreprise),el("p",{},app.status === "sent" ? "Candidature envoyée" : "CV et lettre préparés")),statusTag(app.status)))) : emptyState("Une candidature qui vous ressemble", "Choisissez une offre pour préparer votre CV, votre lettre et votre email.",null,false,"document");
  return el("div",{},heading(`Bonjour ${name} 👋`,"Votre recherche de stage, organisée en toute simplicité.",[button("Ajouter une offre",()=>openOfferModal(),"","plus")]),hero,el("div",{class:"stat-grid"},stat("Offres enregistrées",state.offers.length,"À explorer et à sélectionner","briefcase"),stat("Alertes actives",active,active ? "Vos critères sont enregistrés" : "Créez votre première alerte","bell",active>0),stat("Candidatures préparées",state.applications.length,"Un CV et une lettre adaptés","document"),stat("Candidatures envoyées",sent,sent ? "Votre recherche avance" : "Après votre relecture","mail",sent>0)),searchForm(),el("div",{class:"dashboard-columns"},card("Les dernières opportunités",offerBody,textLink("Toutes les offres",()=>navigate("offers"))),card("Vos candidatures",appBody,textLink("Tout voir",()=>navigate("applications")))));
}
function compactOffer(offer) { return el("div",{class:"offer-item"},monogram(offer.entreprise),el("div",{class:"offer-item-content"},el("h3",{},offer.intitule),el("p",{class:"meta"},[offer.entreprise,offer.lieu].filter(Boolean).join(" · ")),el("div",{class:"tag-row"},tag(contractLabel(offer.contract),"blue"),offer.hybrid ? tag("Hybride") : offer.remote ? tag("Télétravail") : null)),textLink("Voir",()=>openOfferDetail(offer))); }
function contractLabel(contract) { return {stage:"Stage",alternance:"Alternance",cdi:"CDI",cdd:"CDD",all:"Tous contrats"}[String(contract).toLowerCase()] || contract || "À préciser"; }
function renderOffers() {
  const list = el("div",{class:"offers-grid"});
  const query = input("offer_filter","",{placeholder:"Filtrer par titre, entreprise ou ville",type:"search","aria-label":"Filtrer les offres"});
  const caption=el("span",{class:"toolbar-caption"});
  const resultsTab=el("button",{type:"button",class:"results-tab",role:"tab",hidden:searchResults === null},`Dernière recherche (${searchResults?.length || 0})`);
  const savedTab=el("button",{type:"button",class:"results-tab",role:"tab"},`Offres enregistrées (${state.offers.length})`);
  const sourceSummary=compactSearchStatus();
  const renderList = () => {
    const filter = query.value.trim().toLocaleLowerCase("fr");
    const latest = searchResults === null ? [] : searchResults.map(offer=>({...state.offers.find(saved=>saved.id === offer.id),...offer}));
    const base = offersView === "search" && searchResults !== null ? latest : state.offers;
    const offers = base.filter(offer => `${offer.intitule} ${offer.entreprise} ${offer.lieu || ""}`.toLocaleLowerCase("fr").includes(filter));
    if(offersView === "search")offers.sort((a,b)=>relevanceScore(b)-relevanceScore(a));
    const searching=offersView === "search" && searchResults !== null;
    caption.textContent=filter ? `${offers.length} / ${base.length} offre(s) ${searching ? "dans cette recherche" : "enregistrée(s)"}` : `${base.length} offre(s) ${searching ? "dans cette recherche" : "enregistrée(s)"}`;
    for(const [tab,active] of [[resultsTab,searching],[savedTab,!searching]]){tab.classList.toggle("active",active);tab.setAttribute("aria-selected",String(active));}
    sourceSummary.hidden=!searching;
    list.replaceChildren(...offers.map(offerCard));
    if (!offers.length) {
      list.className="";
      const title=filter ? "Aucune offre ne correspond à ce filtre" : searching ? "Aucune offre pour cette recherche" : "Laissez les opportunités venir à vous";
      const description=filter ? "Essayez un autre titre, une entreprise ou une ville." : searching ? "Essayez un rayon plus large ou des mots-clés proches. Vous pouvez aussi consulter vos offres enregistrées ou ajouter une offre repérée ailleurs." : "Recherchez un métier et une ville, ou ajoutez une offre repérée ailleurs. Votre CV et votre lettre seront adaptés à sa description.";
      list.append(emptyState(title,description,filter ? null : button("Importer une offre",()=>openOfferModal(),"primary","plus"),true));
    } else list.className = "offers-grid";
  };
  resultsTab.addEventListener("click",()=>{offersView="search";renderList();});savedTab.addEventListener("click",()=>{offersView="saved";renderList();});
  query.addEventListener("input",renderList); renderList();
  const imports = el("label",{class:"button file-import"},icon("upload"),"Importer un fichier",el("input",{type:"file",accept:".json,application/json","aria-label":"Importer des offres depuis un fichier JSON",onchange:importOffers}));
  return el("div",{},heading("Les bonnes offres, au même endroit","Explorez vos résultats et préparez une candidature adaptée à chaque opportunité.",[imports,button("Ajouter une offre",()=>openOfferModal(),"primary","plus")]),searchForm(),el("div",{class:"sources-hint"},el("p",{},"Les sources publiques sont disponibles sans clé. France Travail peut compléter votre recherche."),textLink("Configurer les sources",()=>openSourceSettings(),"edit")),el("div",{class:"results-tabs",role:"tablist","aria-label":"Vue des offres"},resultsTab,savedTab),sourceSummary,el("div",{class:"list-toolbar"},caption,el("div",{class:"field"},el("div",{class:"input-shell"},icon("search"),query))),list);
}

function compactSearchStatus() {
  const messages=[...new Set(searchWarnings.map(item=>typeof item === "string" ? item : item.message || "Source momentanément indisponible."))];
  const summary=el("div",{class:"search-status-summary"},el("span",{},searchMessage),tag(`${searchSourceStatus.filter(source=>["ok","success","partial","available"].includes(source.status)).length} source(s) consultée(s)`));
  if(!searchSourceStatus.length && !messages.length){summary.lastChild.remove();return el("div",{class:"search-status"},summary);}
  const sourceRows=searchSourceStatus.map(source=>{
    const successful=["ok","success","available"].includes(source.status);
    const inactive=["not_configured","skipped","disabled","unconfigured","out_of_scope"].includes(source.status);
    const partial=source.status === "partial";
    const statusLabel=successful ? `${source.count || 0} offre(s)` : partial ? `${source.count || 0} offre(s) · résultats partiels` : inactive ? "Non consultée" : "Indisponible";
    return el("div",{class:"source-status-row"},el("span",{class:`source-status-dot ${successful?"available":inactive?"inactive":"unavailable"}`,"aria-hidden":"true"}),el("div",{},el("strong",{},source.label || sourceLabel(source.id)),source.message ? el("small",{},source.message) : null),tag(statusLabel,successful?"green":inactive?"":"amber"));
  });
  const details=el("details",{class:"search-status-details"},el("summary",{},messages.length ? `Sources et ${messages.length} information(s) de recherche` : "État des sources consultées"),el("div",{class:"source-status-list"},sourceRows),messages.length ? el("ul",{},messages.map(message=>el("li",{},message))) : null,textLink("Gérer les sources d’offres",()=>openSourceSettings(),"edit"));
  return el("div",{class:"search-status"},summary,details);
}
function offerCard(offer) {
  const existing=state.applications.find(app=>app.id===offer.id || app.offer_id===offer.id);
  return el("article",{class:"offer-card"},el("div",{class:"offer-card-top"},monogram(offer.entreprise),el("div",{},el("div",{class:"offer-company"},offer.entreprise),el("span",{class:"subtle"},offer.lieu || "Localisation non précisée"))),el("h3",{},offer.intitule),el("div",{class:"tag-row"},tag(contractLabel(offer.contract),"blue"),offer.hybrid ? tag("Hybride") : offer.remote ? tag("Télétravail") : null,offer.source ? tag(sourceLabel(offer.source)) : tag("Ajout manuel"),offer.contract_warning ? tag("Contrat à vérifier","amber") : null),el("p",{class:"offer-description"},offer.description || "Consultez l’offre pour en savoir plus sur les missions."),relevanceSummary(offer),offerChecks(offer),el("div",{class:"offer-card-actions"},textLink("Voir l’offre",()=>openOfferDetail(offer)),existing ? button("Ouvrir ma candidature",()=>openApplication(existing),"light","document") : button("Préparer ma candidature",event=>prepareOffer(offer,event.currentTarget),"light","document")));
}
function relevanceScore(offer) { const score=typeof offer.search_relevance === "object" ? offer.search_relevance?.score : offer.search_relevance; return Number.isFinite(Number(score)) ? Number(score) : 0; }
function relevanceSummary(offer) {
  const skills=[...new Set((Array.isArray(offer.matching_skills)?offer.matching_skills:[]).map(skill=>typeof skill === "string" ? skill : skill.label).filter(Boolean))];
  if(skills.length)return el("div",{class:"offer-matches"},el("span",{},icon("check"),`${skills.length} compétence(s) de votre profil`),el("div",{class:"tag-row"},skills.slice(0,4).map(skill=>tag(skill,"green")),skills.length>4?tag(`+${skills.length-4}`):null));
  if(relevanceScore(offer)>0)return el("div",{class:"offer-matches"},el("span",{},icon("check"),"Correspond à votre recherche"));
  return null;
}
function offerChecks(offer) {
  const notes=[...new Set([...(Array.isArray(offer.profile_notes)?offer.profile_notes:[]),...(typeof offer.contract_warning === "string" && offer.contract_warning ? [offer.contract_warning]:[])].map(note=>typeof note === "string"?note:note.message || "").filter(Boolean))];
  const reasons=Array.isArray(offer.search_relevance?.reasons)?offer.search_relevance.reasons:[];
  if(!notes.length && !reasons.length)return null;
  return el("details",{class:"offer-review-notes"},el("summary",{},notes.length?`Points à vérifier avant de postuler (${notes.length})`:"Pourquoi cette offre est proposée"),notes.length?el("ul",{},notes.map(note=>el("li",{},note))):null,reasons.length?el("div",{class:"search-relevance-reasons"},el("strong",{},"Correspondances avec votre recherche"),el("ul",{},reasons.map(reason=>el("li",{},reason)))):null);
}
function sourceLabel(source) { return {all:"Toutes les sources",smartrecruiters:"SmartRecruiters",france_travail:"France Travail","france-travail":"France Travail",arbeitnow:"Arbeitnow",manual:"Ajout manuel",manuel:"Ajout manuel",import:"Import"}[source] || source; }
function openOfferDetail(offer) {
  const existing = state.applications.find(app => app.id === offer.id || app.offer_id === offer.id);
  const body = el("div",{},el("div",{class:"table-company"},monogram(offer.entreprise),el("div",{},el("strong",{},offer.entreprise),el("small",{},offer.lieu || "Localisation non précisée"))),el("div",{class:"tag-row section-spacing"},tag(contractLabel(offer.contract),"blue"),offer.hybrid ? tag("Hybride") : offer.remote ? tag("Télétravail") : null,tag(sourceLabel(offer.source || "manual"))),relevanceSummary(offer),offerChecks(offer),el("div",{class:"offer-detail"},offer.description || "Aucune description fournie. Ajoutez une description pour une adaptation plus précise."),offer.url ? externalLink("Consulter la source",offer.url) : null,el("div",{class:"modal-actions"},button("Fermer",closeModal),existing ? button("Ouvrir la candidature",()=>openApplication(existing),"primary","document") : button("Préparer ma candidature",event=>prepareOffer(offer,event.currentTarget),"primary","document")));
  openModal(offer.intitule,body,true,"UNE OPPORTUNITÉ POUR VOUS");
}
async function prepareOffer(offer,node) {
  await busy(node,async()=>{
    try {
      const result = await api(`/api/offers/${encodeURIComponent(offer.id)}/prepare`,{});
      await loadState();
      const application = result.application || state.applications.find(app=>app.id === offer.id || app.offer_id === offer.id);
      toast(application?.reused || result.reused ? "Votre candidature existante a été ouverte. Vos retouches sont conservées." : "Votre CV, votre lettre et votre email sont prêts à relire.");
      if (application) await openApplication(application); else { closeModal(); navigate("applications"); }
    } catch (error) {
      if (error.code !== "application_incomplete") throw error;
      showIncompleteApplication({id:error.application_id || offer.id,company:offer.entreprise,title:offer.intitule},error);
    }
  });
}
function openOfferModal() {
  const form = el("form",{class:"form-stack"});
  const title = input("intitule","",{required:true,placeholder:"Stage Data Analyst",maxlength:200});
  const company = input("entreprise","",{required:true,placeholder:"Nom de l’entreprise",maxlength:200});
  const location = input("lieu",state.preferences.location || "",{placeholder:"Rennes, France",maxlength:200});
  const description = el("textarea",{name:"description",required:true,rows:8,placeholder:"Collez les missions, le profil recherché et les informations utiles de l’offre…",maxlength:50000});
  const url = input("url","",{type:"url",placeholder:"https://…",maxlength:2000});
  const email = input("contact_email","",{type:"email",placeholder:"recrutement@entreprise.fr",maxlength:254});
  const firstName = input("contact_prenom","",{placeholder:"Prénom du recruteur",maxlength:100});
  const startDate = input("date_debut","",{type:"date"});
  const duration = input("duree_mois","",{type:"number",min:1,max:24,placeholder:"6"});
  const contract = select("contract",[["stage","Stage"],["alternance","Alternance"],["cdi","CDI"],["cdd","CDD"]],"stage");
  const remote = input("remote","",{type:"checkbox"}); const error = errorBox(); const submit = button("Enregistrer l’offre",null,"primary","plus",{type:"submit"});
  form.append(infoBox("Les informations de l’offre servent à sélectionner les compétences et projets déjà présents dans votre parcours."),el("div",{class:"form-row"},field("Intitulé du poste",title),field("Entreprise",company)),el("div",{class:"form-row"},field("Localisation",location),field("Type de contrat",contract)),el("div",{class:"form-row"},field("Début prévu (facultatif)",startDate),field("Durée en mois (facultatif)",duration)),field("Description de l’offre",description),field("Lien vers l’offre (facultatif)",url),el("div",{class:"form-row"},field("Prénom du recruteur (facultatif)",firstName),field("Email du recruteur (facultatif)",email)),el("label",{class:"check-label"},remote,"Télétravail proposé"),error,el("div",{class:"modal-actions"},button("Annuler",closeModal),submit));
  form.addEventListener("submit",event=>{event.preventDefault();busy(submit,async()=>{await api("/api/offers",{intitule:title.value.trim(),entreprise:company.value.trim(),lieu:location.value.trim(),description:description.value.trim(),url:url.value.trim(),contact:{email:email.value.trim(),prenom:firstName.value.trim()},contract:contract.value,remote:remote.checked,source:"manual",...(startDate.value?{date_debut:startDate.value}:{}),...(duration.value?{duree_mois:Number(duration.value)}:{})});offersView="saved";await loadState(false);closeModal();navigate("offers");toast("L’offre a été ajoutée à votre espace.");},error);});
  openModal("Ajouter une offre",form,false,"UNE OFFRE REPÉRÉE AILLEURS ?");
}
async function importOffers(event) {
  const file = event.target.files?.[0]; if (!file) return;
  try { if (file.size > 2 * 1024 * 1024) throw new Error("Le fichier doit faire moins de 2 Mo."); const data = JSON.parse(await file.text()); const offers = Array.isArray(data) ? data : data?.offers || data?.offres || (data?.entreprise && data?.intitule ? [data] : null); if (!Array.isArray(offers)) throw new Error("Le fichier doit contenir une offre ou une liste d’offres JSON."); if (!offers.length) throw new Error("Ce fichier ne contient aucune offre."); await api("/api/offers",{offers}); offersView="saved"; await loadState(); toast(`${offers.length} offre(s) importée(s).`); } catch(error) { toast(error.message,"error"); } finally { event.target.value = ""; }
}

function renderAlerts() {
  const check = button(state.checking ? "Vérification en cours…" : "Vérifier les offres",event=>checkAlerts(event.currentTarget),"","refresh",{disabled:state.checking});
  const intro = infoBox("Une alerte garde vos critères et vous signale les nouvelles correspondances dans cet espace. Les alertes actives sont vérifiées pendant que l’application locale est ouverte."); intro.classList.add("top-hint");
  const grid = state.alerts.length ? el("div",{class:"alert-grid"},state.alerts.map(alertCard)) : emptyState("Votre prochaine opportunité, selon vos critères","Indiquez le type de poste, votre ville et votre rayon de recherche. Chaque nouvelle correspondance sera ajoutée à vos offres.",button("Créer ma première alerte",()=>openAlertModal(),"primary","bell"),true,"bell");
  return el("div",{},heading("Une recherche qui veille pour vous","Vos critères enregistrés, vos nouvelles opportunités à portée de main.",[button("Sources d’offres",()=>openSourceSettings(),"","edit"),check,button("Créer une alerte",()=>openAlertModal(),"primary","plus")]),intro,grid);
}
function alertCard(alert) {
  const toggle = el("button",{type:"button",class:"toggle-button",role:"switch","aria-checked":String(Boolean(alert.enabled)),"aria-label":`${alert.enabled ? "Mettre en pause" : "Activer"} l’alerte ${alert.name || alert.keywords}`,onclick:event=>busy(event.currentTarget,async()=>{await api(`/api/alerts/${encodeURIComponent(alert.id)}`,{enabled:!alert.enabled});await loadState();toast(alert.enabled ? "L’alerte est en pause." : "L’alerte est active.");})});
  const header = el("div",{class:"alert-top"},
    el("div",{class:"alert-name"},el("span",{class:"stat-icon"},icon("bell")),
      el("div",{},el("h3",{},alert.name || alert.keywords),el("p",{},alert.enabled ? "Alerte active" : "En pause"))),toggle);
  const criteria = el("div",{class:"alert-criteria"},
    el("span",{},icon("search"),alert.keywords),
    el("span",{},icon("pin"),alert.location || "Toute la France",alert.location ? ` · ${alert.radius_km || 30} km` : ""),
    el("span",{},icon("briefcase"),contractLabel(alert.contract)));
  const actions = el("div",{class:"alert-actions"},
    el("button",{type:"button",class:"icon-button","aria-label":`Modifier ${alert.name || "l’alerte"}`,onclick:()=>openAlertModal(alert)},icon("edit")),
    el("button",{type:"button",class:"icon-button","aria-label":`Supprimer ${alert.name || "l’alerte"}`,onclick:()=>confirmDeleteAlert(alert)},icon("trash")));
  const footer = el("div",{class:"alert-footer"},el("p",{},alert.last_checked_at ? `Vérifiée le ${shortDate(alert.last_checked_at,true)}` : "Première vérification à venir"),actions);
  return el("article",{class:`alert-card ${alert.enabled ? "" : "paused"}`},header,criteria,
    el("div",{class:"tag-row"},alert.remote ? tag("Télétravail uniquement") : null,alert.source && alert.source!=="all" ? tag(sourceLabel(alert.source)) : null,tag(`${state.offers.filter(o=>(o.alert_ids || []).includes(alert.id)).length} correspondance(s)`,"blue")),
    alert.last_error ? el("p",{class:"muted-note danger-text"},alert.last_error) : null,footer);
}
function openAlertModal(alert = null, initial = null) {
  const defaults = alert || initial || {...(state.preferences || {}),...(settings?.defaults || {}),...(state.search_defaults || {}),...(lastCriteria || {})};
  const name = input("name",alert?.name || "",{placeholder:"Mon stage en Data",maxlength:160});
  const keywords = input("keywords",defaults.keywords || "Data, intelligence artificielle, Business Intelligence",{required:true,maxlength:180,placeholder:"Data, intelligence artificielle"});
  const location = input("location",defaults.location || "",{placeholder:"Ville ou partout en France",list:"location-suggestions",autocomplete:"off",maxlength:180}); wireLocation(location);
  if (defaults.location) {location.dataset.location=defaults.location;location.dataset.cityCode=defaults.city_code||"";if(defaults.latitude != null)location.dataset.latitude=String(defaults.latitude);if(defaults.longitude != null)location.dataset.longitude=String(defaults.longitude);}
  const radius = select("radius_km",[[10,"10 km"],[30,"30 km"],[50,"50 km"],[100,"100 km"],[200,"200 km"]],defaults.radius_km||30);
  const contract = select("contract",[["stage","Stage"],["alternance","Alternance"],["cdi","CDI"],["cdd","CDD"],["all","Tous les contrats"]],defaults.contract||"stage");
  const source = sourceSelect(defaults.source || "all");
  const remote = input("remote","",{type:"checkbox",checked:defaults.remote}); const enabled = input("enabled","",{type:"checkbox",checked:alert ? alert.enabled : true});
  const gps = el("button",{type:"button",class:"icon-button","aria-label":"Utiliser ma position",title:"Utiliser ma position",onclick:event=>useGeolocation(location,event.currentTarget)},icon("target"));
  const error = errorBox(); const submit = button(alert ? "Enregistrer les modifications" : "Créer mon alerte",null,"primary","bell",{type:"submit"});
  const form = el("form",{class:"form-stack"},field("Nom de votre alerte (facultatif)",name),field("Métier ou mots-clés",keywords,"Séparez les alternatives par une virgule. Exemple : data analyst, intelligence artificielle."),field("Localisation",el("div",{class:"input-shell"},icon("pin"),location,gps)),el("div",{class:"form-row"},field("Rayon de recherche",radius),field("Type de contrat",contract)),field("Source d’offres",source),el("label",{class:"check-label"},remote,"Télétravail uniquement"),el("label",{class:"check-label"},enabled,"Activer cette alerte"),infoBox("Les nouvelles offres vous seront signalées ici. Votre candidature reste à préparer et à envoyer après relecture."),error,el("div",{class:"modal-actions"},button("Annuler",closeModal),submit));
  form.addEventListener("submit",event=>{event.preventDefault();busy(submit,async()=>{await resolveTypedLocation(location);await api(alert ? `/api/alerts/${encodeURIComponent(alert.id)}` : "/api/alerts",{name:name.value.trim() || keywords.value.trim(),keywords:keywords.value.trim(),...locationCriteria(location),radius_km:Number(radius.value),contract:contract.value,remote:remote.checked,source:source.value,enabled:enabled.checked});await loadState(false);closeModal();navigate("alerts");toast(alert ? "Votre alerte a été mise à jour." : "Votre alerte est créée.");},error);});
  openModal(alert ? "Modifier votre alerte" : "Créer une alerte",form,false,"VOTRE RECHERCHE, VOS CRITÈRES");
}
function confirmDeleteAlert(alert) {
  const error = errorBox();
  openModal("Supprimer cette alerte ?",el("div",{},el("p",{class:"subtitle"},`« ${alert.name || alert.keywords} » ne sera plus vérifiée. Les offres déjà enregistrées restent accessibles.`),error,el("div",{class:"modal-actions"},button("Conserver",closeModal),button("Supprimer l’alerte",event=>busy(event.currentTarget,async()=>{await api(`/api/alerts/${encodeURIComponent(alert.id)}`,undefined,"DELETE");await loadState(false);closeModal();render();toast("L’alerte a été supprimée.");},error),"danger","trash"))),false,"MES ALERTES");
}
async function checkAlerts(node) {
  await busy(node,async()=>{const result=await api("/api/alerts/check",{});await loadState();const warnings=result.warnings||[];toast(`${result.new_count || 0} nouvelle(s) offre(s) trouvée(s).`,"info");for(const warning of warnings.slice(0,2))toast(typeof warning === "string" ? warning : warning.message || String(warning),"error");});
}

function renderApplications() {
  const query = input("application_filter","",{type:"search",placeholder:"Rechercher une entreprise","aria-label":"Rechercher une candidature"});
  const status = select("status",[["all","Tous les statuts"],["prepared","À relire"],["sent","Envoyées"]],"all"); status.setAttribute("aria-label","Filtrer par statut");
  const rows = el("tbody");
  const update = () => {
    const q=query.value.trim().toLowerCase();const apps=state.applications.filter(app=>`${app.company || app.entreprise} ${app.title || app.intitule || ""}`.toLowerCase().includes(q) && (status.value === "all" || (status.value === "sent" ? ["sent","envoyee"].includes(app.status) : !["sent","envoyee"].includes(app.status))));
    rows.replaceChildren(...apps.map(app=>el("tr",{},el("td",{},el("div",{class:"table-company"},monogram(app.company || app.entreprise),el("div",{},el("strong",{},app.company || app.entreprise),el("small",{},app.title || app.intitule || "Candidature spontanée")))),el("td",{},statusTag(app.status)),el("td",{class:"subtle"},app.created_at ? shortDate(app.created_at) : "Préparée"),el("td",{},el("div",{class:"table-actions"},button("Ouvrir",()=>openApplication(app),"light compact","document"))))));
    if(!apps.length)rows.append(el("tr",{},el("td",{colspan:4},emptyState("Aucune candidature pour le moment","Choisissez une offre pour préparer un CV et une lettre à partir de votre parcours.",textLink("Explorer les offres",()=>navigate("offers")),false,"document"))));
  };
  query.addEventListener("input",update);status.addEventListener("change",update);update();
  return el("div",{},heading("Chaque candidature mérite votre attention","Relisez les documents, choisissez un contact et envoyez quand vous êtes prêt.",[button("Explorer les offres",()=>navigate("offers"),"","briefcase")]),el("div",{class:"info-box top-hint"},el("strong",{},"Votre parcours comme point de départ. "),"Les compétences et projets sont sélectionnés selon l’offre. Relisez les documents et les points à vérifier avant chaque envoi."),el("div",{class:"list-toolbar"},el("div",{class:"field"},el("div",{class:"input-shell"},icon("search"),query)),el("div",{class:"field"},status)),el("div",{class:"table-wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Entreprise / poste"),el("th",{},"Statut"),el("th",{},"Préparation"),el("th",{},"Candidature"))),rows)));
}

function applicationFileLink(app,key,label) {
  const path = app.files?.[key] || `/api/applications/${encodeURIComponent(app.id)}/files/${key}`;
  const safe = safeUrl(path); if(!safe)return null;
  return el("a",{class:"document-link",href:safe,target:"_blank",rel:"noopener noreferrer"},icon("document"),el("span",{},el("strong",{},label),el("small",{},"Ouvrir le document PDF ↗")));
}
async function openApplication(app,recipient = undefined) {
  openModal(app.company || app.entreprise || "Votre candidature",el("div",{class:"loading-screen"},el("span",{class:"spinner"}),el("p",{},"Préparation de l’aperçu…")),true,"CANDIDATURE À RELIRE");
  try {
    const preview = await api(`/api/applications/${encodeURIComponent(app.id)}/preview`,recipient || {});
    if(!dialog.open)return;
    renderApplicationEditor(app,preview);
  } catch(error) {
    if (!dialog.open) return;
    if (error.code === "application_incomplete") showIncompleteApplication(app,error);
    else modalBody.replaceChildren(infoBox(error.message,"error"),el("div",{class:"modal-actions"},button("Fermer",closeModal)));
  }
}
function showIncompleteApplication(app,error) {
  openModal(app.company || app.entreprise || "Votre candidature",el("div",{class:"form-stack"},infoBox(error.message || "Cette candidature est incomplète. Vous pouvez recréer ses documents à partir de l’offre enregistrée.","warning"),el("p",{class:"form-help"},"La régénération remplacera les documents de cette candidature. Conservez une copie de vos retouches avant de confirmer."),el("div",{class:"modal-actions"},button("Fermer",closeModal),error.can_regenerate ? button("Régénérer depuis l’offre",()=>confirmRegenerate({...app,id:error.application_id || app.id},()=>showIncompleteApplication(app,error)),"primary","refresh") : null)),false,"CANDIDATURE À RÉPARER");
}
function renderApplicationEditor(app,preview) {
  const form = el("form",{class:"form-stack"});
  const email = input("email",preview.recipient?.email || app.recipient?.email || "",{type:"email",required:true,placeholder:"prenom@entreprise.fr",maxlength:254});
  const firstName = input("prenom",preview.recipient?.prenom || app.recipient?.prenom || "",{placeholder:"Prénom du recruteur (facultatif)",maxlength:100});
  const subject = input("subject",preview.subject || "",{required:true,maxlength:998});
  const body = el("textarea",{name:"body",class:"email-textarea",rows:14,required:true,maxlength:50000},preview.body || "");
  const letter = el("textarea",{name:"letter",class:"letter-textarea",rows:18,maxlength:50000},preview.letter_text || "");
  let savedLetter = preview.letter_text || "";
  const error = errorBox();
  const matchingContacts=state.contacts.filter(c=>(c.entreprise || c.company || "").toLowerCase()===(app.company || app.entreprise || "").toLowerCase());
  const contactChoice=select("contact",[["","Choisir un contact enregistré"],...matchingContacts.map((c,i)=>[String(i),[c.prenom,c.nom].filter(Boolean).join(" ") + ` · ${c.email}`])],"");
  contactChoice.addEventListener("change",()=>{const c=matchingContacts[Number(contactChoice.value)];if(contactChoice.value!==""&&c){email.value=c.email;firstName.value=c.prenom||"";}});
  const contactPanel=el("div",{class:"application-contact-panel",hidden:true});
  const selectContact=contact=>{email.value=contact.email || "";firstName.value=contact.prenom || "";contactPanel.hidden=true;toast("Le destinataire a été renseigné. Vos textes sont conservés.");};
  const showEditor=()=>openModal(app.company || app.entreprise || "Votre candidature",form,true,"CANDIDATURE À RELIRE");
  const tabContent=el("div");
  const emailTab=el("button",{type:"button",class:"tab-button active",role:"tab","aria-selected":"true"},"Email de candidature");
  const letterTab=el("button",{type:"button",class:"tab-button",role:"tab","aria-selected":"false"},"Lettre de motivation");
  const emailContent=el("div",{class:"form-stack"},field("Objet",subject),field("Votre message",body),el("p",{class:"form-help"},"Vous pouvez corriger le texte avant de l’enregistrer ou de l’envoyer."));
  const saveLetter=button("Enregistrer la lettre et son PDF",event=>busy(event.currentTarget,async()=>{await api(`/api/applications/${encodeURIComponent(app.id)}/letter`,{text:letter.value});savedLetter=letter.value;preview.letter_text=letter.value;toast("La lettre et son PDF ont été mis à jour.");},error),"light","save");
  const letterContent=el("div",{class:"form-stack"},field("Votre lettre",letter),saveLetter);
  const setTab = (showLetter)=>{emailTab.classList.toggle("active",!showLetter);emailTab.setAttribute("aria-selected",String(!showLetter));letterTab.classList.toggle("active",showLetter);letterTab.setAttribute("aria-selected",String(showLetter));tabContent.replaceChildren(showLetter?letterContent:emailContent);};
  emailTab.addEventListener("click",()=>setTab(false));letterTab.addEventListener("click",()=>setTab(true));setTab(false);
  const refreshPreview=button("Actualiser l’aperçu",event=>busy(event.currentTarget,async()=>{const updated=await api(`/api/applications/${encodeURIComponent(app.id)}/preview`,{email:email.value.trim(),prenom:firstName.value.trim()});if(body.value===(preview.body||""))body.value=updated.body||body.value;if(subject.value===(preview.subject||""))subject.value=updated.subject||subject.value;preview=updated;toast("L’aperçu a été actualisé pour votre destinataire.");},error),"ghost compact","refresh");
  const saveDraft=button("Enregistrer le mail",event=>busy(event.currentTarget,async()=>{await api(`/api/applications/${encodeURIComponent(app.id)}/draft`,{subject:subject.value,body:body.value});toast("Votre mail est enregistré.");},error),"","save");
  const send=button("Relire et envoyer",event=>{
    if(!email.reportValidity()||!subject.reportValidity()||!body.reportValidity())return;
    busy(event.currentTarget,async()=>{
      if(letter.value!==savedLetter){await api(`/api/applications/${encodeURIComponent(app.id)}/letter`,{text:letter.value});savedLetter=letter.value;preview.letter_text=letter.value;toast("La lettre modifiée et son PDF ont été enregistrés.");}
      reviewSend(app,{email:email.value.trim(),prenom:firstName.value.trim(),subject:subject.value,body:body.value},preview,showEditor);
    },error);
  },"primary","mail");
  const regenerate=button("Régénérer les documents",()=>confirmRegenerate(app,showEditor),"ghost compact","refresh");
  const notes=[...(Array.isArray(preview.notes)?preview.notes:[]),...(Array.isArray(preview.warnings)?preview.warnings:[])];
  const editorParts = [
    el("p",{class:"subtitle"},app.title || app.intitule || "Candidature spontanée"),
    app.status === "sent" ? infoBox("Cette candidature a déjà été envoyée. Vous pouvez consulter ses documents et son destinataire.","warning") : null,
    el("div",{class:"document-links"},applicationFileLink(app,"cv","Votre CV personnalisé"),applicationFileLink(app,"letter","Votre lettre de motivation")),
    el("div",{},el("div",{class:"section-header"},el("h3",{},"À qui souhaitez-vous écrire ?"),refreshPreview),
      matchingContacts.length?field("Contact de l’entreprise",contactChoice):null,
      el("div",{class:"recipient-form"},field("Email du destinataire",email),field("Prénom du destinataire",firstName)),
      textLink("Rechercher un contact RH",()=>{
        contactPanel.hidden=false;
        if (!contactPanel.hasChildNodes()) {
          const search=createContactSearch({company:app.company || app.entreprise,onSelect:selectContact,onClose:()=>{contactPanel.hidden=true;}});
          contactPanel.append(search.node); search.search(false);
        }
      },"users"),contactPanel),
    el("div",{class:"application-tabs",role:"tablist","aria-label":"Documents à relire"},emailTab,letterTab),tabContent,
    notes.length?el("details",{class:"detail-notes"},el("summary",{},`${notes.length} point(s) à vérifier avant l’envoi`),el("ul",{},notes.map(note=>el("li",{},typeof note === "string"?note:note.message||String(note))))):null,
    error,el("div",{class:"application-actions"},regenerate,el("div",{},saveDraft,send))
  ];
  form.append(...editorParts.filter(Boolean));
  form.addEventListener("submit",event=>event.preventDefault());
  showEditor();
}
function confirmRegenerate(app,back = ()=>openApplication(app)) {
  const error=errorBox();
  openModal("Régénérer cette candidature ?",el("div",{},el("p",{class:"subtitle"},"Le CV, la lettre et le mail seront recréés à partir de votre profil et de l’offre. Les textes personnalisés seront remplacés."),error,el("div",{class:"modal-actions"},button("Retour",back),button("Régénérer",event=>busy(event.currentTarget,async()=>{await api(`/api/applications/${encodeURIComponent(app.id)}/regenerate`,{confirmed:true});await loadState();await openApplication(app);toast("Les documents ont été régénérés.");},error),"primary","refresh"))),false,"VOTRE CANDIDATURE");
}
function reviewSend(app,draft,preview,back) {
  const error=errorBox();const enabled=Boolean(state.integrations.smtp);
  const send=button("Confirmer l’envoi de cet email",event=>busy(event.currentTarget,async()=>{const result=await api(`/api/applications/${encodeURIComponent(app.id)}/send`,{...draft,confirmed:true});await loadState(false);closeModal();navigate("applications");toast(result.message||"Votre candidature a été envoyée.");},error),"primary","mail",{disabled:!enabled});
  const docs=(preview.attachments || []).map(item=>externalLink(item.name,item.url,"button compact"));
  openModal("Tout est prêt pour l’envoi ?",el("div",{},el("p",{class:"subtitle"},"Vérifiez une dernière fois le destinataire, le message et les pièces jointes."),el("div",{class:"send-review section-spacing"},el("p",{},el("strong",{},"À : "),[draft.prenom,draft.email].filter(Boolean).join(" · ")),el("p",{},el("strong",{},"Objet : "),draft.subject),el("div",{class:"send-body"},draft.body)),el("div",{class:"document-links"},docs.length?docs:[applicationFileLink(app,"cv","CV joint"),applicationFileLink(app,"letter","Lettre jointe")]),!enabled?infoBox("L’envoi d’emails n’est pas encore connecté. Votre candidature peut être relue et enregistrée. Les instructions de connexion sont dans Mon profil.","warning"):null,error,el("div",{class:"modal-actions spaced"},button("Retour à la relecture",back),send)),true,"DERNIÈRE RELECTURE");
}

function contactDate(value) {
  const date=new Date(value);
  return value && !Number.isNaN(date.getTime()) ? new Intl.DateTimeFormat("fr-FR",{day:"numeric",month:"short",year:"numeric",timeZone:"Europe/Paris"}).format(date) : "Date non renseignée";
}
function contactCard(contact,onSelect = null) {
  const name=[contact.prenom,contact.nom].filter(Boolean).join(" ") || "Contact de l’entreprise";
  const sources=Array.isArray(contact.public_sources) ? contact.public_sources.filter(source=>safeUrl(source.url)) : [];
  const statusLabels={valid:"Valide",invalid:"Invalide",accept_all:"Domaine acceptant tout",unknown:"Inconnu",webmail:"Webmail",disposable:"Temporaire",risky:"À vérifier",blocked:"Bloqué"};
  const status=contact.verification_status;
  const verification=status ? el("p",{class:"contact-verification"},el("strong",{},`Statut Hunter : ${statusLabels[status] || status}`),contact.verification_date ? el("small",{},`Vérifié le ${contactDate(contact.verification_date)}`) : null) : null;
  const reasons=Array.isArray(contact.priority_reasons) ? contact.priority_reasons : [];
  const confidence=Number(contact.confiance);
  const details=el("div",{class:"contact-evidence"},
    contact.linkedin ? externalLink("Profil LinkedIn",contact.linkedin) : null,
    verification,
    contact.confiance !== "" && contact.confiance != null && Number.isFinite(confidence) ? el("p",{class:"contact-confidence"},`Indice Hunter : ${confidence}/100`) : null,
    sources.length ? el("details",{class:"contact-sources"},el("summary",{},`${sources.length} source(s) publique(s)`),el("ul",{},sources.map((source,index)=>el("li",{},externalLink(`Source ${index+1}`,source.url),el("small",{},source.last_seen_on ? `Dernière observation : ${contactDate(source.last_seen_on)}` : "Date d’observation non renseignée"))))) : el("p",{class:"form-help"},contact.source === "manual" ? "Contact ajouté manuellement. Vérifiez son adresse et sa source." : "Source publique non renseignée pour ce contact enregistré."),
    reasons.length ? el("div",{class:"contact-priority"},el("strong",{},"Pourquoi ce contact ?"),el("ul",{},reasons.map(reason=>el("li",{},reason)))) : null);
  return el("article",{class:"contact-card","data-contact-email":contact.email || ""},el("span",{class:"avatar"},initials(name)),el("h3",{},name),el("p",{class:"contact-role"},[contact.poste,contact.entreprise || contact.company].filter(Boolean).join(" · ") || "Poste non renseigné"),contact.email ? el("a",{class:"contact-email",href:`mailto:${encodeURIComponent(contact.email)}`},contact.email) : el("p",{},"Email non renseigné"),el("div",{class:"tag-row"},contact.department ? tag(({hr:"Ressources humaines",it:"Informatique",management:"Management",research:"Recherche",consulting:"Conseil"})[contact.department] || contact.department) : null,tag(contact.source === "manual" ? "Ajout manuel" : contact.source === "hunter" ? "Hunter" : "Contact enregistré")),details,onSelect ? button("Choisir ce contact",()=>onSelect(contact),"light compact contact-select","check") : null);
}
function createContactSearch({company:initialCompany = "",onSelect = null,onClose = null} = {}) {
  const company=input("contact_company",initialCompany,{required:true,placeholder:"Nom de l’entreprise",maxlength:200});
  if(initialCompany) company.readOnly=true;
  const domain=input("contact_domain","",{placeholder:"entreprise.fr",maxlength:253,autocomplete:"off"});
  const error=errorBox();
  const status=el("div",{class:"contact-search-status",role:"status","aria-live":"polite"});
  const warnings=el("div",{class:"contact-search-warnings"});
  const results=el("div",{class:"contact-results"});
  const queries=el("div",{class:"contact-query-links"});
  let pending=false;
  const searchButton=button("Rechercher les contacts",()=>search(false),"primary compact","search");
  const refreshButton=button("Actualiser la recherche",()=>search(true),"ghost compact","refresh",{hidden:true});
  const node=el("section",{class:"contact-search", "aria-label":"Recherche de contacts de l’entreprise"},el("div",{class:"section-header"},el("div",{},el("h3",{},"Trouver les bons interlocuteurs"),el("p",{class:"form-help"},"RH d’abord, puis responsables et collaborateurs si nécessaire.")),onClose ? button("Fermer la recherche",onClose,"ghost compact") : null),el("div",{class:"form-row"},field("Entreprise",company),field("Domaine de l’entreprise (facultatif)",domain,"Précisez-le pour distinguer les entreprises homonymes.")),el("p",{class:"form-help"},state.integrations.hunter ? "Une recherche utilise au maximum trois requêtes Hunter. Les résultats sont conservés 24 heures ; l’actualisation relance Hunter." : "Hunter n’est pas connecté. Vous pouvez consulter les contacts disponibles et les liens de recherche de profils."),el("div",{class:"contact-search-actions"},searchButton,refreshButton),error,status,warnings,results,queries);
  async function search(force) {
    if(pending || !company.reportValidity())return;
    pending=true; searchButton.disabled=true;refreshButton.disabled=true;searchButton.setAttribute("aria-busy","true");error.textContent="";
    status.replaceChildren(el("span",{class:"spinner","aria-hidden":"true"}),el("span",{},"Recherche des contacts et de leurs sources publiques…"));
    try {
      const result=await api("/api/contacts/search",{company:company.value.trim(),department:"hr",domain:domain.value.trim(),force:Boolean(force)});
      await loadState(false);
      const contacts=Array.isArray(result.contacts) ? result.contacts.slice(0,10) : [];
      status.replaceChildren(el("strong",{},`${contacts.length} contact(s) disponible(s)`),result.resolved_domain ? tag(`Domaine : ${result.resolved_domain}`) : null,tag(result.from_cache ? "Résultats conservés" : "Recherche actualisée",result.from_cache ? "" : "blue"));
      const messages=Array.isArray(result.warnings) ? result.warnings : [];
      warnings.replaceChildren(...messages.map(message=>infoBox(typeof message === "string" ? message : message.message || "Recherche partiellement disponible.","warning")));
      results.replaceChildren(...contacts.map(contact=>contactCard(contact,onSelect)));
      if(!contacts.length)results.append(el("p",{class:"contact-no-results"},"Aucun email accompagné d’une source publique n’a été trouvé. Explorez les profils et pages professionnelles ci-dessous, ou ajoutez un contact manuellement."));
      if(result.truncated)results.append(el("p",{class:"form-help"},"Recherche limitée à trois requêtes Hunter et dix contacts prioritaires au maximum."));
      const links=(Array.isArray(result.queries) ? result.queries : []).map(query=>externalLink(query.label || "Rechercher un profil",query.url)).filter(Boolean);
      queries.replaceChildren(...(links.length ? [el("h4",{},"Poursuivre sur les profils publics"),el("div",{},links),el("p",{class:"form-help"},"Ces liens ouvrent une recherche. Aucun profil LinkedIn connecté n’est parcouru automatiquement.")] : []));
      refreshButton.hidden=false;
    } catch(searchError) {error.textContent=searchError.message;status.replaceChildren(el("span",{},"La recherche n’a pas abouti. Les contacts précédents restent disponibles."));}
    finally {pending=false;searchButton.disabled=false;refreshButton.disabled=false;searchButton.removeAttribute("aria-busy");}
  }
  for(const fieldNode of [company,domain])fieldNode.addEventListener("keydown",event=>{if(event.key === "Enter"){event.preventDefault();search(false);}});
  return {node,search};
}
function renderContacts() {
  const query=input("contact_filter","",{type:"search",placeholder:"Un nom, un poste, une entreprise, un email","aria-label":"Rechercher un contact"});
  const grid=el("div",{class:"contact-cards"});
  const update=()=>{
    const q=query.value.toLocaleLowerCase("fr");
    const contacts=state.contacts.filter(contact=>[contact.prenom,contact.nom,contact.poste,contact.entreprise || contact.company,contact.email].filter(Boolean).join(" ").toLocaleLowerCase("fr").includes(q));
    grid.replaceChildren(...contacts.map(contact=>contactCard(contact)));
    if(!contacts.length){grid.className="";grid.append(emptyState("Le bon contact fait la différence","Ajoutez une personne repérée dans une offre ou recherchez les contacts RH d’une entreprise.",button("Ajouter un contact",()=>openContactModal(),"primary","plus"),true,"users"));}else grid.className="contact-cards";
  };
  query.addEventListener("input",update);update();
  return el("div",{},heading("Des contacts pour ouvrir la conversation","Retrouvez les interlocuteurs, leurs profils et les sources de leurs emails.",[button("Chercher un contact RH",()=>openHunterModal(),"","search"),button("Ajouter un contact",()=>openContactModal(),"primary","plus")]),el("div",{class:"list-toolbar"},el("span",{class:"toolbar-caption"},`${state.contacts.length} contact(s) enregistré(s)`),el("div",{class:"field"},el("div",{class:"input-shell"},icon("search"),query))),grid);
}
function openContactModal() {
  const first=input("prenom","",{placeholder:"Prénom",maxlength:100});const last=input("nom","",{placeholder:"Nom",maxlength:100});const company=input("entreprise","",{required:true,placeholder:"Entreprise",maxlength:200});const email=input("email","",{type:"email",required:true,placeholder:"prenom@entreprise.fr",maxlength:254});
  const role=input("poste","",{placeholder:"Talent Acquisition, responsable Data…",maxlength:300});
  const linkedin=input("linkedin","",{type:"url",placeholder:"https://www.linkedin.com/in/…",maxlength:2000});
  const sourceUrl=input("source_url","",{type:"url",placeholder:"https://entreprise.fr/equipe",maxlength:2000});
  const error=errorBox();const submit=button("Enregistrer le contact",null,"primary","save",{type:"submit"});
  const form=el("form",{class:"form-stack"},el("div",{class:"form-row"},field("Prénom",first),field("Nom",last)),field("Entreprise",company),field("Poste (facultatif)",role),field("Email professionnel",email),field("Profil LinkedIn (facultatif)",linkedin),field("Page publique où figure l’email (facultatif)",sourceUrl),error,el("div",{class:"modal-actions"},button("Annuler",closeModal),submit));
  form.addEventListener("submit",event=>{event.preventDefault();busy(submit,async()=>{await api("/api/contacts",{prenom:first.value.trim(),nom:last.value.trim(),entreprise:company.value.trim(),email:email.value.trim(),poste:role.value.trim(),linkedin:linkedin.value.trim(),source_url:sourceUrl.value.trim(),source:"manual"});await loadState(false);closeModal();navigate("contacts");toast("Votre contact a été enregistré.");},error);});openModal("Ajouter un contact",form,false,"VOTRE CARNET D’ADRESSES");
}
function openHunterModal() {
  const search=createContactSearch({onClose:()=>{closeModal();navigate("contacts");}});
  openModal("Trouver les bons interlocuteurs",search.node,true,"RECHERCHE DE CONTACTS RH");
}

async function openSourceSettings() {
  openModal("Sources d’offres",el("div",{class:"loading-screen"},el("span",{class:"spinner"}),el("p",{},"Chargement des paramètres…")),false,"VOTRE RECHERCHE");
  try {
    settings=await api("/api/settings");
    if(dialog.open)renderSourceSettings();
  } catch(error) { if(dialog.open)modalBody.replaceChildren(infoBox(error.message,"error"),el("div",{class:"modal-actions"},button("Fermer",closeModal))); }
}
function renderSourceSettings() {
  const config=settings?.france_travail || {};
  const sources=Array.isArray(settings?.sources) ? settings.sources : [];
  const sourceList=el("div",{class:"settings-sources"},sources.map(source=>el("div",{class:"settings-source"},
    el("span",{class:`source-status-dot ${source.configured?"available":"inactive"}`,"aria-hidden":"true"}),
    el("div",{},el("strong",{},source.label || sourceLabel(source.id)),el("small",{},source.description || (source.requires_credentials ? "Source complémentaire avec accès API." : "Source publique d’offres."))),
    tag(source.requires_credentials ? source.configured ? "Configuré" : "Facultatif" : "Sans clé",source.configured?"green":""))));
  const clientId=input("france_travail_client_id","",{autocomplete:"off",spellcheck:"false",maxlength:1000,placeholder:config.configured ? "Laisser vide pour conserver" : "Identifiant de votre application",required:!config.configured});
  const clientSecret=input("france_travail_client_secret","",{type:"password",autocomplete:"off",spellcheck:"false",maxlength:4000,placeholder:config.configured ? "Laisser vide pour conserver" : "Secret de votre application",required:!config.configured});
  const error=errorBox();
  const submit=button("Enregistrer la configuration",null,"primary","save",{type:"submit"});
  const help=el("details",{class:"source-access-help"},el("summary",{},"Comment obtenir un accès France Travail ?"),el("p",{},"Renseignez les identifiants d’une application déjà autorisée à utiliser l’API Offres d’emploi. Si vous n’avez pas cet accès, vous pouvez le demander à France Travail."),el("div",{class:"source-help-links"},externalLink("Demander l’accès API",config.contact_url || "https://francetravail.io/contact"),externalLink("Consulter l’API officielle",config.docs_url || "https://francetravail.io/data/api/offres-emploi"),externalLink("Portail France Travail",config.portal_url || "https://francetravail.io/")));
  const form=el("form",{class:"form-stack source-configuration"},
    el("div",{class:"section-header"},el("div",{},el("h3",{},"France Travail"),el("p",{},"Un complément facultatif aux sources publiques.")),tag(config.configured?"Configuré":"Sans identifiants",config.configured?"green":"")),
    help,field("Identifiant de l’application",clientId),field("Secret de l’application",clientSecret),
    el("p",{class:"form-help"},config.configured ? "Les champs vides conservent vos identifiants. Les identifiants enregistrés ne sont jamais réaffichés." : "Votre recherche fonctionne déjà avec les sources publiques. Ajoutez ces identifiants lorsque votre accès API est autorisé."),
    error,el("div",{class:"modal-actions spaced"},config.configured?button("Retirer France Travail",()=>confirmClearFranceTravail(),"ghost compact","trash"):button("Fermer",closeModal),submit));
  form.addEventListener("submit",event=>{
    event.preventDefault();
    busy(submit,async()=>{
      const result=await api("/api/settings",{france_travail_client_id:clientId.value.trim(),france_travail_client_secret:clientSecret.value.trim()});
      clientId.value="";clientSecret.value="";
      settings=result.settings || (Array.isArray(result.sources)?result:await api("/api/settings"));
      await loadState(false);render();
      if(dialog.open)renderSourceSettings();
      toast("La configuration des sources a été enregistrée.");
    },error);
  });
  openModal("Sources d’offres",el("div",{},el("p",{class:"subtitle"},"SmartRecruiters et les sources publiques peuvent être consultés sans clé. Choisissez une source dans votre recherche ou laissez toutes les sources disponibles."),sourceList,form),false,"VOTRE RECHERCHE");
}
function confirmClearFranceTravail() {
  const error=errorBox();
  openModal("Retirer les identifiants France Travail ?",el("div",{},el("p",{class:"subtitle"},"Cette source ne sera plus consultée jusqu’à l’enregistrement de nouveaux identifiants. Les sources publiques, vos offres et vos candidatures restent accessibles."),error,el("div",{class:"modal-actions"},button("Conserver",renderSourceSettings),button("Retirer les identifiants",event=>busy(event.currentTarget,async()=>{const result=await api("/api/settings",{clear_france_travail:true});settings=result.settings || (Array.isArray(result.sources)?result:await api("/api/settings"));await loadState(false);render();renderSourceSettings();toast("Les identifiants France Travail ont été retirés.");},error),"danger","trash"))),false,"PARAMÈTRES DES SOURCES");
}

function renderProfile() {
  const profile=state.profile||{},identity=profile.identity||{},internship=profile.internship||{};
  const identityCard=card("Votre parcours",el("div",{class:"card-body"},el("div",{class:"profile-identity"},el("span",{class:"avatar"},initials(identity.display_name)),el("div",{},el("h2",{},identity.display_name || [identity.first_name,identity.last_name].filter(Boolean).join(" ")),el("p",{},profile.education?.[0]?.title || "Votre profil candidat"))),el("p",{class:"compact-summary"},profile.summary||""),el("div",{class:"profile-section"},el("h3",{},"Vos compétences"),el("div",{class:"tag-row"},(profile.skills||[]).map(skill=>tag(typeof skill === "string"?skill:skill.label)))),el("div",{class:"profile-section"},el("h3",{},"Vos projets"),(profile.projects||[]).map(project=>el("div",{class:"timeline-item"},el("h3",{},project.title),el("p",{},project.type || project.period || ""))))),textLink("Modifier",()=>openProfileModal(),"edit"));
  const details=[];if(identity.email)details.push(el("li",{},icon("mail"),identity.email));if(identity.phone)details.push(el("li",{},icon("phone"),identity.phone));if(identity.linkedin)details.push(el("li",{},icon("link"),externalLink("LinkedIn",identity.linkedin)));if(identity.github)details.push(el("li",{},icon("link"),externalLink("GitHub",identity.github)));
  const availability=card("Vos coordonnées & disponibilités",el("div",{class:"card-body"},el("ul",{class:"detail-list"},details),el("div",{class:"profile-section"},el("h3",{},"Votre stage"),el("div",{class:"tag-row"},tag(`${internship.duration_months || 6} mois`,"blue"),tag(internship.mobility || "Mobilité à préciser"),internship.available_from?tag(`Dès ${shortDate(internship.available_from)}`):null)),el("div",{class:"profile-section"},el("h3",{},"Votre localisation de recherche"),el("p",{},state.preferences.location || "Choisissez une ville dans votre recherche. Votre lieu d’études ne renseigne pas automatiquement votre localisation."))));
  const integration=(label,description,connected)=>el("div",{class:"integration-item"},el("div",{},el("strong",{},label),el("small",{},description)),tag(connected?"Connecté":"À connecter",connected?"green":""));
  const franceTravailStatus=el("div",{class:"integration-item"},el("div",{},el("strong",{},"Offres France Travail"),el("small",{},"Source complémentaire facultative")),tag(state.integrations.france_travail?"Configuré":"Sans identifiants",state.integrations.france_travail?"green":""));
  const connections=card("Vos connexions",el("div",{class:"card-body"},integration("Recherche de contacts","Hunter",state.integrations.hunter),integration("Envoi de candidatures","Votre messagerie SMTP",state.integrations.smtp),franceTravailStatus,button("Configurer les sources d’offres",()=>openSourceSettings(),"light full-width","edit"),el("p",{class:"muted-note"},"Les sources publiques fonctionnent sans clé. Les paramètres Hunter et de messagerie sont décrits dans le guide du projet.")));
  return el("div",{},heading("Votre profil, la base de chaque candidature","Vos informations réelles guident les CV, les lettres et les emails préparés.",[button("Modifier mes informations",()=>openProfileModal(),"","edit")]),el("div",{class:"profile-grid"},identityCard,el("div",{class:"form-stack"},availability,connections)),el("div",{class:"section-spacing"},infoBox("Les compétences, formations et projets viennent de votre CV. Une candidature met en avant les éléments pertinents et signale les compétences demandées qui ne sont pas attestées par votre parcours.")));
}
function openProfileModal() {
  const profile=state.profile||{},identity=profile.identity||{},internship=profile.internship||{};
  const first=input("first_name",identity.first_name,{required:true,maxlength:150});const last=input("last_name",identity.last_name,{required:true,maxlength:150});const display=input("display_name",identity.display_name,{required:true,maxlength:300});const email=input("email",identity.email,{type:"email",required:true,maxlength:254});const phone=input("phone",identity.phone,{maxlength:60});const linkedin=input("linkedin",identity.linkedin,{type:"url",maxlength:2000});const github=input("github",identity.github,{type:"url",maxlength:2000});const duration=input("duration_months",internship.duration_months||6,{type:"number",min:1,max:24,required:true});const available=input("available_from",internship.available_from,{type:"date",required:true});const mobility=input("mobility",internship.mobility,{maxlength:300});const summary=el("textarea",{name:"summary",rows:5,maxlength:5000},profile.summary||"");const error=errorBox();const submit=button("Enregistrer mon profil",null,"primary","save",{type:"submit"});
  const form=el("form",{class:"form-stack"},el("div",{class:"form-row"},field("Prénoms",first),field("Nom",last)),field("Nom affiché sur les documents",display),el("div",{class:"form-row"},field("Email",email),field("Téléphone",phone)),el("div",{class:"form-row"},field("LinkedIn",linkedin),field("GitHub",github)),el("div",{class:"form-row"},field("Durée du stage (mois)",duration),field("Disponible à partir du",available)),field("Mobilité",mobility),field("Présentation du profil",summary),infoBox("Les candidatures déjà préparées gardent leurs documents. Pour appliquer les nouvelles informations, ouvrez une candidature et choisissez « Régénérer les documents »."),error,el("div",{class:"modal-actions"},button("Annuler",closeModal),submit));
  form.addEventListener("submit",event=>{event.preventDefault();busy(submit,async()=>{await api("/api/profile",{identity:{first_name:first.value.trim(),last_name:last.value.trim(),display_name:display.value.trim(),email:email.value.trim(),phone:phone.value.trim(),linkedin:linkedin.value.trim(),github:github.value.trim()},internship:{duration_months:Number(duration.value),available_from:available.value,mobility:mobility.value.trim()},summary:summary.value.trim()});await loadState(false);closeModal();navigate("profile");toast("Votre profil a été enregistré.");},error);});openModal("Modifier vos informations",form,true,"VOTRE PROFIL CANDIDAT");
}
async function openNotifications() {
  const items=state.notifications;
  openModal("Vos nouveautés",items.length?el("div",{},items.slice().reverse().map(item=>el("article",{class:`notification-item ${item.read?"":"unread"}`},el("h3",{},item.title || "Nouvelle opportunité"),el("p",{},item.message || ""),el("small",{},shortDate(item.created_at,true)))),el("div",{class:"modal-actions"},button("Voir les offres",()=>{closeModal();navigate("offers");},"primary","briefcase"))):emptyState("Vous êtes à jour","Les nouvelles correspondances de vos alertes apparaîtront ici.",null,false,"bell"),false,"CE QUI A CHANGÉ DEPUIS VOTRE VISITE");
  if(items.some(item=>!item.read)){try{await api("/api/notifications/read",{});await loadState(false);}catch(error){toast(error.message,"error");}}
}

$("#modal-close").addEventListener("click",closeModal);
dialog.addEventListener("click",event=>{if(event.target!==dialog)return;const bounds=dialog.getBoundingClientRect();if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)closeModal();});
$("#notifications-button").addEventListener("click",()=>{if(state)openNotifications();});
window.addEventListener("hashchange",()=>{render();window.scrollTo({top:0,behavior:"instant"});});
async function start() {
  try {
    const results=await Promise.allSettled([loadState(false),api("/api/settings")]);
    if(results[0].status === "rejected")throw results[0].reason;
    if(results[1].status === "fulfilled")settings=results[1].value;
    render();
  }
  catch(error){content.replaceChildren(el("div",{class:"connection-error"},el("h2",{},"Votre espace n’est pas encore disponible"),el("p",{},error.message),button("Réessayer",event=>busy(event.currentTarget,start),"primary","refresh")));}
}
start();
window.setInterval(async()=>{if(document.hidden||dialog.open||!state)return;try{const previousNotifications=state.notifications.length;const previousOffers=state.offers.length;const previousChecking=state.checking;await loadState(false);if(state.notifications.length>previousNotifications){toast("De nouvelles opportunités correspondent à vos alertes.","info");if(["dashboard","alerts","offers"].includes(currentPage))render();}else if(state.offers.length!==previousOffers||state.checking!==previousChecking) {if(["dashboard","alerts"].includes(currentPage))render();}}catch{/* A temporary connection issue will not interrupt editing. */}},30000);

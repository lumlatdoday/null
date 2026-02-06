let backend = null;

const state = {
  profiles: [],
  selected: new Set(),
  menuOpen: false,
  menuProfile: null,
  closeTimer: null,
  toastTimer: null,
  menuEl: null,
  searchOpen: false,
  searchQuery: "",
  topMenuOpen: false,
  topMenuEl: null,
  editProfileId: null,
  editProfileFolder: "",
  proxyCheckReqId: null,
  isCreateMode: false,
};

function el(id){ return document.getElementById(id); }
function asId(x){ return String(x ?? ""); }

/* ===================== View Navigation ===================== */

function showProfilesView(){
  const pv = el("profilesView");
  const sv = el("settingsView");
  const ev = el("editView");

  if(pv) pv.classList.remove("hidden");
  if(sv) sv.classList.add("hidden");
  if(ev) ev.classList.add("hidden");

  const pl = el("topProfilesLeft");
  const sl = el("topSettingsLeft");
  if(pl) pl.classList.remove("hidden");
  if(sl) sl.classList.add("hidden");
}

function showSettingsView(){
  const pv = el("profilesView");
  const sv = el("settingsView");
  const ev = el("editView");

  if(pv) pv.classList.add("hidden");
  if(sv) sv.classList.remove("hidden");
  if(ev) ev.classList.add("hidden");

  const pl = el("topProfilesLeft");
  const sl = el("topSettingsLeft");
  if(pl) pl.classList.add("hidden");
  if(sl) sl.classList.remove("hidden");
}

function showEditView(){
  const pv = el("profilesView");
  const sv = el("settingsView");
  const ev = el("editView");

  if(pv) pv.classList.add("hidden");
  if(sv) sv.classList.add("hidden");
  if(ev) ev.classList.remove("hidden");

  const pl = el("topProfilesLeft");
  const sl = el("topSettingsLeft");
  if(pl) pl.classList.add("hidden");
  if(sl) sl.classList.remove("hidden");
}

function setSettingsPage(page){
  const ps = el("pageSettings");
  const pa = el("pageAbout");
  if(ps) ps.classList.toggle("hidden", page !== "settings");
  if(pa) pa.classList.toggle("hidden", page !== "about");

  const bs = el("navSettings");
  const ba = el("navAbout");
  if(bs) bs.classList.toggle("active", page === "settings");
  if(ba) ba.classList.toggle("active", page === "about");
}

function openAboutView(){
  showSettingsView();
  setSettingsPage("about");

  const b = el("aboutBuild");
  if(b){
    const d = new Date();
    const pad = (n)=> String(n).padStart(2,"0");
    b.textContent = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
}

/* ===================== Settings ====================== */

function fillSettingsForm(data){
  if(el("inpChromePath")) el("inpChromePath").value = (data && data.chromePath) ? data.chromePath : "";
  if(el("inpProfilesDir")) el("inpProfilesDir").value = (data && data.profilesDir) ? data.profilesDir : "";
}

function readSettingsForm(){
  return {
    chromePath: (el("inpChromePath") ? el("inpChromePath").value : "").trim(),
    profilesDir: (el("inpProfilesDir") ? el("inpProfilesDir").value : "").trim(),
  };
}

function openSettingsView(){
  showSettingsView();
  setSettingsPage("settings");

  if(!backend || !backend.getSettings){
    showToast("Backend does not implement getSettings() yet. (Python patch required)");
    return;
  }
  backend.getSettings(function(json){
    let data = {};
    try{ data = JSON.parse(json || "{}"); }catch(e){ data = {}; }
    fillSettingsForm(data);
  });
}

function saveSettings(){
  if(!backend || !backend.saveSettings){
    showToast("Backend does not implement saveSettings() yet. (Python patch required)");
    return;
  }
  const v = readSettingsForm();
  backend.saveSettings(v.chromePath, v.profilesDir, function(ok){
    showToast(ok ? "Saved." : "Save failed.");
  });
}

function resetSettings(){
  if(!backend || !backend.resetSettings){
    showToast("Backend does not implement resetSettings() yet. (Python patch required)");
    return;
  }
  backend.resetSettings(function(ok){
    if(ok){
      showToast("Reset.");
      openSettingsView();
    }else{
      showToast("Reset failed.");
    }
  });
}

function browseChrome(){
  if(!backend || !backend.browseChrome){
    showToast("Backend does not implement browseChrome() yet. (Python patch required)");
    return;
  }
  backend.browseChrome(function(path){
    if(path && el("inpChromePath")) el("inpChromePath").value = path;
  });
}

function browseProfiles(){
  if(!backend || !backend.browseProfiles){
    showToast("Backend does not implement browseProfiles() yet. (Python patch required)");
    return;
  }
  backend.browseProfiles(function(path){
    if(path && el("inpProfilesDir")) el("inpProfilesDir").value = path;
  });
}

/* ===================== UI Repaint Helpers (QtWebEngine) ===================== */

function forceRepaint(node){
  if(!node) return;
  node.style.outline = "1px solid transparent";
  void node.offsetHeight;
  requestAnimationFrame(()=>{ node.style.outline = ""; });
}

function forceRepaintHard(node){
  if(!node) return;
  const prev = node.style.visibility;
  node.style.visibility = "hidden";
  void node.offsetHeight;
  node.style.visibility = prev || "";
}

/**
 * Fix delayed repaint for custom checkbox (GoLogin-like)
 * Some QtWebEngine/WebView builds repaint :checked a bit late (often updates on hover/mouseout).
 * This forces a small repaint after click/change.
 */
function installCheckboxRepaintFix() {
  const kick = (cb) => {
    try { cb.blur(); } catch (e) {}
    requestAnimationFrame(() => forceRepaintHard(cb));
  };

  document.addEventListener("change", (e) => {
    const t = e.target;
    if (!(t instanceof HTMLInputElement)) return;
    if (t.type !== "checkbox") return;
    if (!t.classList.contains("chk")) return;
    kick(t);
  }, true);

  document.addEventListener("click", (e) => {
    const node = e.target;
    if (!(node instanceof HTMLElement)) return;

    let cb = null;
    if (node.matches?.('input.chk[type="checkbox"]')) {
      cb = node;
    } else {
      const lbl = node.closest("label");
      if (lbl) cb = lbl.querySelector('input.chk[type="checkbox"]');
    }
    if (!cb) return;
    kick(cb);
  }, true);
}

/* ===================== Selection & Bulk Helpers ===================== */

function pruneSelection(){
  const ids = new Set(state.profiles.map(p => asId(p.id)));
  for(const id of Array.from(state.selected)){
    if(!ids.has(id)) state.selected.delete(id);
  }
}

function updateHeaderCheckbox(){
  const all = el("chkAll");
  if(!all) return;

  const list = visibleProfiles();
  const total = list.length;

  all.disabled = (total === 0);
  all.indeterminate = false;

  let allOn = (total > 0);
  for(const p of list){
    const id = asId(p.id);
    if(!state.selected.has(id)){
      allOn = false;
      break;
    }
  }
  all.checked = allOn;

  const tbody = el("tbody");
  if(tbody) tbody.classList.toggle("all-selected", all.checked);

  const btnDelAll = el("btnDeleteAll");
  if(btnDelAll){
    if(state.selected.size >= 2){
      btnDelAll.classList.remove("hidden");
    } else {
      btnDelAll.classList.add("hidden");
    }
  }

  requestAnimationFrame(()=>forceRepaintHard(all));
}

function setAllRowCheckboxesDOM(checked){
  const tbody = el("tbody");
  if(!tbody) return;

  const arr = Array.from(tbody.querySelectorAll('input.chk[data-pid]'));

  let i = 0;
  const BATCH = 400;

  function step(){
    const end = Math.min(i + BATCH, arr.length);
    for(; i < end; i++){
      const box = arr[i];
      box.checked = checked;

      const row = box.closest("tr");
      if(row){
        if(checked) row.classList.add("is-checked");
        else row.classList.remove("is-checked");
      }
    }
    if(i < arr.length){
      requestAnimationFrame(step);
    }else{
      requestAnimationFrame(()=>forceRepaintHard(tbody));
    }
  }

  step();
}

/* ===================== Delete Dialog ===================== */

let pendingDelete = null;

function truncateEnd(s, maxLen){
  s = (s ?? "").toString();
  if(maxLen <= 3) return s.slice(0, maxLen);
  if(s.length <= maxLen) return s;
  return s.slice(0, maxLen - 3) + "...";
}

function openDeleteDlg(p){
  pendingDelete = p;
  const title = document.getElementById("dlgTitle");
  if(title){
    if(p && p.isBulk){
        title.style.whiteSpace = "normal";
        title.style.lineHeight = "1.5";
        title.innerHTML = "Delete all selected profiles.<br>Are you sure?";
    } else {
        title.style.whiteSpace = "";
        title.style.lineHeight = "";
        const name = (p && p.name != null) ? String(p.name) : "";
        title.textContent = name ? `Delete profile ${name}` : "Delete profile";
    }
  }

  const ov = document.getElementById("dlgOverlay");
  if(ov){
    ov.classList.add("open");
    ov.setAttribute("aria-hidden", "false");
  }
}

function closeDeleteDlg(){
  pendingDelete = null;
  const ov = document.getElementById("dlgOverlay");
  if(ov){
    ov.classList.remove("open");
    ov.setAttribute("aria-hidden", "true");
  }
}

/* ===================== Common UI Helpers ===================== */

function norm(s){ return (s||"").toString().toLowerCase(); }

function visibleProfiles(){
  const q = norm(state.searchQuery).trim();
  if(!q) return state.profiles;
  return state.profiles.filter(p => norm(p && p.name).includes(q));
}

function setLoading(on){
  const l = el("loading");
  if(!l) return;

  if(on){
    l.classList.remove("hidden");
    l.setAttribute("aria-hidden", "false");
  }else{
    l.classList.add("hidden");
    l.setAttribute("aria-hidden", "true");
  }

  const b = el("btnRefresh");
  if(b) b.disabled = !!on;

  requestAnimationFrame(()=>forceRepaintHard(l));
}

function showToast(msg){
  const t = el("toast");
  if(!t) return;
  t.textContent = msg || "";
  t.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(()=>t.classList.remove("show"), 1300);
}

function statusLabel(s){
  s = norm(s);
  if(s === "running") return "running";
  if(s === "error") return "error";
  if(s === "checking") return "checking";
  if(s === "conn-error") return "conn-error";
  return "ready";
}

/* ===================== Custom Select Controls ===================== */

function closeAllCSelect(exceptEl){
  document.querySelectorAll(".csel.open").forEach((x)=>{
    if(exceptEl && x === exceptEl) return;
    x.classList.remove("open");
    const btn = x.querySelector(".csel-btn");
    if(btn) btn.setAttribute("aria-expanded","false");
  });
}

function setCSelectValue(root, value){
  if(!root) return;
  const input = root.querySelector('input[type="hidden"]');
  const label = root.querySelector(".csel-value");
  const items = Array.from(root.querySelectorAll(".csel-item"));

  let picked = null;
  for(const it of items){
    const ok = (String(it.dataset.value || "") === String(value));
    it.classList.toggle("active", ok);
    if(ok) picked = it;
  }

  if(input) input.value = String(value ?? "");
  if(label){
    label.textContent = picked ? picked.textContent : String(value ?? "");
  }
}

/* ===================== Edit Profile (Tabs, Form, Proxy) ===================== */

function resetProxyStatus(){
  const s = el("epProxyStatus");
  if(s) {
    s.textContent = "Not checked yet";
    s.classList.remove("is-live", "is-die");
  }
}

function updateProxyAuthEnabled(){
  const t = (el("epProxyType") ? el("epProxyType").value : "http").toLowerCase();
  const u = el("epProxyUser");
  const p = el("epProxyPass");
  if(!u || !p) return;

  const isS4 = (t === "socks4");
  u.disabled = isS4;
  p.disabled = isS4;

  if(isS4){
    u.value = "";
    p.value = "";
    u.placeholder = "SOCKS4 does not support authentication";
    p.placeholder = "SOCKS4 does not support authentication";
  }else{
    u.placeholder = "optional";
    p.placeholder = "optional";
  }
}

function setEditTab(tab){
  const tabs = document.querySelectorAll(".ep-tab");
  tabs.forEach(b => b.classList.toggle("active", b.dataset.tab === tab));

  const pages = document.querySelectorAll(".ep-page");
  pages.forEach(p => p.classList.toggle("hidden", p.dataset.page !== tab));

  closeAllCSelect(null);
}

function fillEditForm(data){
  data = data || {};

  if(el("epName")) el("epName").value = data.name || "";
  if(el("epNotes")) el("epNotes").value = data.notes || "";

  const os = String(data.os || "windows").toLowerCase();
  setCSelectValue(el("epOsSel"), os);

  const pr = (data.proxy && typeof data.proxy === "object") ? data.proxy : {};
  if(el("epProxyEnabled")) el("epProxyEnabled").checked = !!pr.enabled;

  const ptype = String(pr.type || "http").toLowerCase();
  setCSelectValue(el("epProxyTypeSel"), ptype);

  if(el("epProxyHost")) el("epProxyHost").value = pr.host || "";
  if(el("epProxyPort")) el("epProxyPort").value = (pr.port === 0 || pr.port) ? String(pr.port) : "";
  if(el("epProxyUser")) el("epProxyUser").value = pr.username || "";
  if(el("epProxyPass")) el("epProxyPass").value = pr.password || "";

  updateProxyAuthEnabled();
  resetProxyStatus();

  const w = (data.webrtc && typeof data.webrtc === "object") ? data.webrtc : {};
  const mode = String(w.mode || "altered").toLowerCase();
  setCSelectValue(el("epWebrtcSel"), mode);

  setEditTab("general");
}

function readEditForm(){
  const name = (el("epName") ? el("epName").value : "").trim();
  const os = (el("epOs") ? el("epOs").value : "windows").trim().toLowerCase();
  const notes = (el("epNotes") ? el("epNotes").value : "");

  const enabled = !!(el("epProxyEnabled") && el("epProxyEnabled").checked);
  const ptype = (el("epProxyType") ? el("epProxyType").value : "http").trim().toLowerCase();
  const host = (el("epProxyHost") ? el("epProxyHost").value : "").trim();
  const portRaw = (el("epProxyPort") ? el("epProxyPort").value : "").trim();
  const username = (el("epProxyUser") ? el("epProxyUser").value : "");
  const password = (el("epProxyPass") ? el("epProxyPass").value : "");

  const mode = (el("epWebrtcMode") ? el("epWebrtcMode").value : "altered").trim().toLowerCase();

  let port = "";
  if(portRaw){
    const n = parseInt(portRaw, 10);
    port = Number.isFinite(n) ? n : "";
  }

  return {
    name,
    os,
    notes,
    proxy: {
      enabled,
      type: ptype,
      host,
      port,
      username,
      password
    },
    webrtc: { mode }
  };
}

function openEditProfile(p){
  if(!p) return;

  state.isCreateMode = false;
  state.editProfileId = asId(p.id);
  state.editProfileFolder = (p.folder || "");

  showEditView();

  const title = document.querySelector(".edit-title");
  if(title) title.textContent = "Edit Profile";

  const btnSave = el("epSave");
  if(btnSave) btnSave.textContent = "Save Profile";

  const divQty = el("epFieldQty");
  if(divQty) divQty.classList.add("hidden");

  const divOs = el("epFieldOs");
  if(divOs) divOs.classList.add("hidden");

  fillEditForm({
    name: p.name || "",
    os: p.os || "windows",
    notes: p.notes || "",
    proxy: {},
    webrtc: {mode:"altered"}
  });

  if(backend && backend.getProfileDetail){
    backend.getProfileDetail(state.editProfileId, function(json){
      let d = null;
      try{ d = JSON.parse(json || "{}"); }catch(e){ d = null; }
      if(d && typeof d === "object"){
        fillEditForm(d);
      }
    });
  }
}

function openCreateProfile(){
  state.isCreateMode = true;
  state.editProfileId = null;

  showEditView();

  const title = document.querySelector(".edit-title");
  if(title) title.textContent = "Create Profile";

  const btnSave = el("epSave");
  if(btnSave) btnSave.textContent = "Create Profile";

  const defaultName = getNextProfileName();

  fillEditForm({
    name: defaultName,
    os: "windows",
    notes: "",
    proxy: {enabled: false, type: "http", host:"", port:"", username:"", password:""},
    webrtc: {mode: "altered"}
  });

  const qtySel = el("epQtySel");
  if(qtySel) setCSelectValue(qtySel, "1");

  const divQty = el("epFieldQty");
  if(divQty) divQty.classList.remove("hidden");

  const divOs = el("epFieldOs");
  if(divOs) divOs.classList.remove("hidden");

  const inpName = el("epName");
  if(inpName){
      inpName.onblur = function() {
          if(!this.value.trim()){
              this.value = getNextProfileName();
          }
      };
  }

  setEditTab("general");
}

/* ===================== Context Menu ===================== */

function getNextProfileName() {
  let maxNum = 0;
  const regex = /^New Profile (\d+)$/i;

  state.profiles.forEach(p => {
    const name = (p.name || "").trim();
    const match = name.match(regex);
    if (match) {
      const num = parseInt(match[1], 10);
      if (!isNaN(num) && num > maxNum) {
        maxNum = num;
      }
    }
  });

  return `New Profile ${maxNum + 1}`;
}

function ensureMenu(){
  if(state.menuEl) return state.menuEl;

  const m = document.createElement("div");
  m.className = "menu-float";
  m.id = "ctxMenu";
  m.innerHTML = `
    <div class="menu-item" id="miEdit"><span class="menu-ico">⚙</span><span>Edit</span></div>
    <div class="menu-sep"></div>
    <div class="menu-item" id="miDelete"><span class="menu-ico">🗑</span><span>Delete</span></div>
  `;

  m.addEventListener("mouseenter", ()=>cancelClose());
  m.addEventListener("mouseleave", ()=>scheduleClose());

  document.body.appendChild(m);
  state.menuEl = m;

  m.querySelector("#miEdit").addEventListener("click", ()=>{
    if(!state.menuProfile || !backend) return;
    const p = state.menuProfile;
    closeMenu();
    openEditProfile(p);
  });

  m.querySelector("#miDelete").addEventListener("click", ()=>{
    if(!state.menuProfile || !backend) return;
    const p = state.menuProfile;
    closeMenu();
    openDeleteDlg(p);
  });

  return m;
}

/* ===================== Top Menu ===================== */

function ensureTopMenu(){
  if(state.topMenuEl) return state.topMenuEl;

  const m = document.createElement("div");
  m.className = "menu-float";
  m.id = "topMenu";
  m.innerHTML = `
    <div class="menu-item" id="miSettings">
      <span class="menu-ico">⚙</span><span>Settings</span>
    </div>
    <div class="menu-item" id="miAbout">
      <span class="menu-ico">ℹ</span><span>About</span>
    </div>
  `;

  document.body.appendChild(m);
  state.topMenuEl = m;

  m.querySelector("#miSettings").addEventListener("click", ()=>{
    closeTopMenu();
    openSettingsView();
  });

  m.querySelector("#miAbout").addEventListener("click", ()=>{
    closeTopMenu();
    openAboutView();
  });

  return m;
}

function openTopMenuAt(anchorEl){
  const m = ensureTopMenu();

  closeMenu();

  state.topMenuOpen = true;

  const r = anchorEl.getBoundingClientRect();
  const gap = 8;

  m.style.top = `${Math.round(r.bottom + gap)}px`;
  m.style.left = "0px";
  m.classList.add("open");

  const mw = m.offsetWidth || 190;
  let left = Math.round(r.right - mw);
  left = Math.max(10, Math.min(left, window.innerWidth - mw - 10));
  m.style.left = `${left}px`;
}

function closeTopMenu(){
  if(!state.topMenuEl) return;
  state.topMenuEl.classList.remove("open");
  state.topMenuOpen = false;
}

function toggleTopMenu(anchorEl){
  if(state.topMenuOpen) closeTopMenu();
  else openTopMenuAt(anchorEl);
}

function openMenuFor(profile, anchorEl){
  const m = ensureMenu();

  state.menuProfile = profile;
  state.menuOpen = true;

  const r = anchorEl.getBoundingClientRect();
  const gap = 6;

  m.style.visibility = "hidden";
  m.classList.add("open");

  const menuHeight = m.offsetHeight;
  const menuWidth = m.offsetWidth || 190;
  const windowHeight = window.innerHeight;

  let top = r.bottom + gap;

  if (top + menuHeight > windowHeight && r.top > menuHeight) {
      top = r.top - menuHeight - gap;

      m.classList.add("flipped");
  } else {
      m.classList.remove("flipped");
  }

  let left = Math.round(r.right - menuWidth);
  left = Math.max(10, Math.min(left, window.innerWidth - menuWidth - 10));

  m.style.top = `${Math.round(top)}px`;
  m.style.left = `${left}px`;

  m.style.visibility = "";
}

function closeMenu(){
  if(!state.menuEl) return;
  state.menuEl.classList.remove("open");
  state.menuOpen = false;
  state.menuProfile = null;
}

function scheduleClose(){
  clearTimeout(state.closeTimer);
  state.closeTimer = setTimeout(()=>closeMenu(), 140);
}
function cancelClose(){
  clearTimeout(state.closeTimer);
  state.closeTimer = null;
}

/* ===================== Rendering ===================== */

function render(){
  const tbody = el("tbody");
  tbody.innerHTML = "";
  const q = norm(state.searchQuery).trim();
  tbody.classList.toggle("searching", !!q);

  const list = visibleProfiles();

  for(const p of list){
    const tr = document.createElement("tr");

    const td0 = document.createElement("td");
    const cbWrap = document.createElement("label");
    cbWrap.className = "chk-wrap";
    const cb = document.createElement("input");
    cb.className = "chk";
    cb.type = "checkbox";
    const pid = String(p.id ?? "");
    cb.dataset.pid = pid;
    cb.checked = state.selected.has(pid);
    if(state.selected.has(pid)) tr.classList.add("is-checked");
    cbWrap.appendChild(cb);
    td0.appendChild(cbWrap);

    const td1 = document.createElement("td");
    const nameCell = document.createElement("div");
    nameCell.className = "name-cell";
    const nameLeft = document.createElement("div");
    nameLeft.className = "name-left";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = p.name || "";
    nameLeft.appendChild(name);
    nameCell.appendChild(nameLeft);
    td1.appendChild(nameCell);

    const td2 = document.createElement("td");
    const st = document.createElement("div");
    const sl = statusLabel(p.status);

    let labelText = sl;
    if(sl === "conn-error") labelText = "No connection";
    if(sl === "checking") labelText = "Checking proxy";

    st.className = `state ${sl}`;
    st.innerHTML = `<span class="ring"></span><span class="label">${labelText}</span>`;
    td2.appendChild(st);

    const tdNotes = document.createElement("td");
    const nt = document.createElement("div");
    nt.className = "notes";
    nt.dataset.pid = pid;
    const noteText = (p.notes ?? "").toString();
    if(noteText.trim()){
      nt.textContent = noteText;
    } else {
      nt.innerHTML = `<span class="note-add">+ Add note</span>`;
    }
    tdNotes.appendChild(nt);

    const td3 = document.createElement("td");
    const pr = document.createElement("div");
    pr.className = "proxy";
    pr.textContent = p.proxy || "";
    td3.appendChild(pr);

    const wrap = document.createElement("div");
    wrap.className = "actions name-actions";

    const statusRaw = norm(p.status);

    if(statusRaw === "running"){
      const btnView = document.createElement("button");
      btnView.className = "a-btn teal";
      btnView.textContent = "View";
      btnView.addEventListener("click", ()=>{
        if(!backend) return;
        backend.viewProfile(String(p.id))
      });

      const btnStop = document.createElement("button");
      btnStop.className = "a-btn red";
      btnStop.textContent = "Stop";
      btnStop.addEventListener("click", ()=>{
        if(!backend) return;
        backend.stopProfile(p.id);
      });

      wrap.appendChild(btnView);
      wrap.appendChild(btnStop);
    }
    else if(statusRaw === "checking"){
        const btnLoad = document.createElement("button");
        btnLoad.className = "a-btn teal";
        btnLoad.disabled = true;
        btnLoad.innerHTML = `<span class="btn-spinner"></span>`;
        wrap.appendChild(btnLoad);
    }
    else {
      const btnRun = document.createElement("button");
      btnRun.className = "a-btn teal";
      btnRun.textContent = "Run";
      btnRun.addEventListener("click", ()=>{
        if(!backend) return;
        backend.startProfile(p.id);
      });
      wrap.appendChild(btnRun);
    }

    const kebab = document.createElement("button");
    kebab.className = "kebab";
    kebab.innerHTML = `<span class="kebab-ico" aria-hidden="true"></span>`;
    kebab.addEventListener("mouseenter", ()=>{
      cancelClose();
      openMenuFor(p, kebab);
    });
    kebab.addEventListener("mouseleave", ()=>scheduleClose());

    wrap.appendChild(kebab);
    nameCell.appendChild(wrap);

    tr.appendChild(td0);
    tr.appendChild(td1);
    tr.appendChild(td2);
    tr.appendChild(tdNotes);
    tr.appendChild(td3);

    tbody.appendChild(tr);
  }
  updateHeaderCheckbox();
}

/* ===================== Qt WebChannel Bridge ===================== */

function setupWebChannel(){
  if(typeof qt === "undefined"){
    showToast("Qt bridge is not ready yet.");
    return;
  }

  new QWebChannel(qt.webChannelTransport, function(channel){
    backend = channel.objects.backend;

    backend.profilesChanged.connect(function(json){
      try{ state.profiles = JSON.parse(json || "[]"); } catch(e){ state.profiles = []; }
      pruneSelection();
      render();
	  setLoading(false);
    });

    backend.toast.connect(function(msg){ showToast(msg); });
    if(backend.proxyChecked){
      backend.proxyChecked.connect(function(reqId, json){
        reqId = String(reqId || "");
        if(!reqId) return;
        if(state.proxyCheckReqId && reqId !== state.proxyCheckReqId) return;

        let d = null;
        try{ d = JSON.parse(json || "{}"); }catch(e){ d = null; }

        const s = el("epProxyStatus");
        const btn = el("epProxyCheck");
        if(btn) btn.disabled = false;

        if(!s) return;

        if(d && typeof d === "object" && Object.prototype.hasOwnProperty.call(d, "ok")){
          const ok = !!d.ok;
          const msg = (d.msg != null) ? String(d.msg) : "";

          s.textContent = msg;

          if(ok) {
              s.classList.add("is-live");
              s.classList.remove("is-die");
          } else {
              s.classList.remove("is-live");
              s.classList.add("is-die");
          }

        }else{
          s.textContent = "Error parsing result.";
          s.classList.remove("is-live", "is-die");
        }
      });
    }

    setLoading(true);
    requestAnimationFrame(()=>backend.refresh());
  });
}

/* ===================== DOM Wiring ===================== */

document.addEventListener("DOMContentLoaded", ()=>{
	installCheckboxRepaintFix();
    const tbody = el("tbody");
    if(tbody){

      let _hoverTr = null;

      function _setHoverTr(tr){
        if(_hoverTr === tr) return;
        if(_hoverTr) _hoverTr.classList.remove("is-hover");
        _hoverTr = tr;
        if(_hoverTr) _hoverTr.classList.add("is-hover");
      }

      tbody.addEventListener("mousemove", (e)=>{
        const tr = e.target && e.target.closest ? e.target.closest("tr") : null;
        if(!tr || tr.parentElement !== tbody) return;
        _setHoverTr(tr);
      });

      tbody.addEventListener("mouseleave", ()=>{
        _setHoverTr(null);
      });

      tbody.addEventListener("input", (e)=>{
        const t = e.target;
        if(!t || !t.classList || !t.classList.contains("chk")) return;

        const pid = t.dataset.pid;
        if(!pid) return;

        if(t.checked) state.selected.add(pid);
        else state.selected.delete(pid);

        updateHeaderCheckbox();

        const row = t.closest("tr");
        if(row){
          if(t.checked) row.classList.add("is-checked");
          else row.classList.remove("is-checked");
        }

        t.blur();
        forceRepaint(row || t);
      });
    }

	const chkAll = el("chkAll");
    if(chkAll){
      chkAll.addEventListener("click", (e)=>e.stopPropagation());

      chkAll.addEventListener("input", ()=>{
        const checked = chkAll.checked;

        const list = visibleProfiles();
        for(const p of list){
          const id = asId(p.id);
          if(checked) state.selected.add(id);
          else state.selected.delete(id);
        }

        setAllRowCheckboxesDOM(checked);
        updateHeaderCheckbox();
        chkAll.blur();
      });

      updateHeaderCheckbox();
    }
  document.addEventListener("click", (e)=>{
    const t = e.target;
    if(state.menuEl && state.menuEl.contains(t)) return;
    if(t.closest(".kebab")) return;
    closeMenu();
  });

  window.addEventListener("scroll", ()=>{ closeMenu(); closeTopMenu(); }, true);
  window.addEventListener("resize", ()=>{ closeMenu(); closeTopMenu(); });

  const btnAdd = el("btnAdd");
  if(btnAdd){
    btnAdd.addEventListener("click", ()=>{
      if(!backend) return;
      openCreateProfile();
    });
  }

  const btnMenu = el("btnMenu");
  if(btnMenu){
    btnMenu.addEventListener("click", (e)=>{
      e.preventDefault();
      e.stopPropagation();
      toggleTopMenu(btnMenu);
    });
  }

  document.addEventListener("mousedown", (e)=>{
    if(!state.topMenuOpen) return;

    const insideMenu = e.target && e.target.closest && e.target.closest("#topMenu");
    const onButton   = e.target && e.target.closest && e.target.closest("#btnMenu");
    if(insideMenu || onButton) return;

    closeTopMenu();
  });

  const btnSearch = el("btnSearch");
  const searchBox = el("searchBox");
  const searchInput = el("searchInput");
  const btnSearchClear = el("btnSearchClear");
  const searchWrap = el("searchWrap");

  function openSearch(){
    if(!searchBox || !searchInput) return;
    searchBox.classList.remove("hidden");
    searchBox.setAttribute("aria-hidden", "false");
    state.searchOpen = true;
	if(searchWrap) searchWrap.classList.add("open");
    searchInput.focus();
  }

  function closeSearch(){
  if(!searchBox) return;

  searchBox.classList.add("hidden");
  searchBox.setAttribute("aria-hidden", "true");
  state.searchOpen = false;
  if(searchWrap) searchWrap.classList.remove("open");

  if(searchInput) searchInput.value = "";
  if(state.searchQuery){
    state.searchQuery = "";
    render();
  }else{
    render();
  }
}

  function applySearch(){
    if(!searchInput) return;
    state.searchQuery = searchInput.value || "";
    render();
  }

  if(btnSearch){
    btnSearch.addEventListener("click", ()=>{
      if(!state.searchOpen){
        openSearch();
        return;
      }
      applySearch();
    });
  }

  if(searchInput){

    searchInput.addEventListener("keydown", (e)=>{
      if(e.key === "Enter"){
        e.preventDefault();
        applySearch();
        searchInput.blur();
      }else if(e.key === "Escape"){
        e.preventDefault();
        closeSearch();
      }
    });
  }

  if(btnSearchClear){
    btnSearchClear.addEventListener("click", (e)=>{
      e.preventDefault();
      e.stopPropagation();
      if(!searchInput) return;

      const v = (searchInput.value || "").trim();

      if(v){
        searchInput.value = "";
        state.searchQuery = "";
        render();
        searchInput.focus();
      }else{
        closeSearch();
      }
    });
  }

  document.addEventListener("mousedown", (e)=>{
    if(!state.searchOpen) return;

    const inside = e.target && e.target.closest && e.target.closest("#searchWrap");
    if(inside) return;

    const v = (searchInput && searchInput.value) ? searchInput.value.trim() : "";
    if(v) return;

    closeSearch();
  });

  if(tbody){

    function _notesShow(noteBox, text){
      const v = (text ?? "").toString();
      if(v.trim()){
        noteBox.textContent = v;
      }else{
        noteBox.innerHTML = `<span class="note-add">+ Add note</span>`;
      }
      noteBox.classList.remove("editing");
    }

    function _startEdit(noteBox){
      if(noteBox.classList.contains("editing")) return;

      const pid = noteBox.dataset.pid || "";
      if(!pid) return;

      const p = state.profiles.find(x => asId(x.id) === pid);
      const current = (p && p.notes) ? String(p.notes) : "";

      noteBox.classList.add("editing");
      noteBox.innerHTML = `<input class="note-input" type="text" spellcheck="false" />`;

      const inp = noteBox.querySelector(".note-input");
      inp.value = current;
      inp.focus();

      let done = false;

      const commit = () => {
        if(done) return;
        done = true;

        const val = inp.value;

        if(p) p.notes = val;
        _notesShow(noteBox, val);

        if(backend && backend.setNote){
          backend.setNote(pid, val);
        }
      };

      const cancel = () => {
        if(done) return;
        done = true;
        _notesShow(noteBox, current);
      };

      inp.addEventListener("keydown", (ev) => {
        if(ev.key === "Enter"){
          ev.preventDefault();
          inp.blur();
        }else if(ev.key === "Escape"){
          ev.preventDefault();
          cancel();
        }
      });

      inp.addEventListener("blur", () => {
        commit();
      });
    }

    tbody.addEventListener("click", (e) => {
      const t = e.target;

      if(t && t.classList && t.classList.contains("note-input")) return;

      const noteBox = t.closest(".notes");
      if(!noteBox) return;

      e.preventDefault();
      e.stopPropagation();

      _startEdit(noteBox);
    });
  }

  const btnRefresh = el("btnRefresh") || el("#");
  if(btnRefresh){
    btnRefresh.addEventListener("click", () => {
      if(!backend) return;
      setLoading(true);
      requestAnimationFrame(()=>backend.refresh());
    });
  }

  const btnDelAll = el("btnDeleteAll");
  if(btnDelAll){
    btnDelAll.addEventListener("click", () => {
        openDeleteDlg({ isBulk: true });
    });
  }

  if(btnOk){
      const newBtnOk = btnOk.cloneNode(true);
      btnOk.parentNode.replaceChild(newBtnOk, btnOk);

      newBtnOk.addEventListener("click", () => {
        if(!pendingDelete) return closeDeleteDlg();
        const p = pendingDelete;
        closeDeleteDlg();

        const be = (window.backend || backend);
        if(!be || !be.deleteProfile) return;

        if(p.isBulk){
            const ids = Array.from(state.selected);

            if(be.deleteProfiles){
                be.deleteProfiles(JSON.stringify(ids));
            } else {
                ids.forEach(id => be.deleteProfile(id));
            }

            state.selected.clear();
            updateHeaderCheckbox();
        } else {
            be.deleteProfile(p.id);
        }
      });
  }

  const btnBack = el("btnBack");
  if(btnBack) btnBack.addEventListener("click", ()=>showProfilesView());

  const btnBrowseChrome = el("btnBrowseChrome");
  if(btnBrowseChrome) btnBrowseChrome.addEventListener("click", ()=>browseChrome());

  const btnBrowseProfiles = el("btnBrowseProfiles");
  if(btnBrowseProfiles) btnBrowseProfiles.addEventListener("click", ()=>browseProfiles());

  const btnSaveSettings = el("btnSaveSettings");
  if(btnSaveSettings) btnSaveSettings.addEventListener("click", ()=>saveSettings());

  const btnResetSettings = el("btnResetSettings");
  if(btnResetSettings) btnResetSettings.addEventListener("click", ()=>resetSettings());

  const navSettings = el("navSettings");
  if(navSettings) navSettings.addEventListener("click", ()=>openSettingsView());

  const navAbout = el("navAbout");
  if(navAbout) navAbout.addEventListener("click", ()=>openAboutView());

  document.querySelectorAll(".csel").forEach((root)=>{
    const btn = root.querySelector(".csel-btn");
    const menu = root.querySelector(".csel-menu");
    if(!btn || !menu) return;

    btn.addEventListener("click", (e)=>{
      e.preventDefault();
      e.stopPropagation();

      const isOpen = root.classList.contains("open");
      closeAllCSelect(root);
      root.classList.toggle("open", !isOpen);
      btn.setAttribute("aria-expanded", (!isOpen) ? "true" : "false");
    });

    menu.addEventListener("click", (e)=>{
      const it = e.target && e.target.closest ? e.target.closest(".csel-item") : null;
      if(!it) return;

      const v = it.dataset.value;
      setCSelectValue(root, v);

      const hid = root.querySelector('input[type="hidden"]');
      if(hid) hid.value = String(v ?? "");

      root.classList.remove("open");
      btn.setAttribute("aria-expanded","false");

      if(root.id === "epProxyTypeSel"){
        updateProxyAuthEnabled();
        resetProxyStatus();
      }
      if(root.id === "epWebrtcSel"){
        const hm = el("epWebrtcMode");
        if(hm) hm.value = String(v ?? "");
      }
      if(root.id === "epOsSel"){
        const ho = el("epOs");
        if(ho) ho.value = String(v ?? "");
      }
    });
  });

  document.addEventListener("click", ()=>closeAllCSelect(null));
  window.addEventListener("scroll", ()=>closeAllCSelect(null), true);
  window.addEventListener("resize", ()=>closeAllCSelect(null));

  document.querySelectorAll(".ep-tab").forEach((b)=>{
    b.addEventListener("click", ()=>{
      setEditTab(b.dataset.tab);
    });
  });

  ["epProxyEnabled","epProxyHost","epProxyPort","epProxyUser","epProxyPass"].forEach((id)=>{
    const x = el(id);
    if(!x) return;
    x.addEventListener("input", resetProxyStatus);
    x.addEventListener("change", resetProxyStatus);
  });

  const btnPaste = el("epProxyPaste");
  if(btnPaste){
    btnPaste.addEventListener("click", ()=>{
      if(!backend || !backend.clipboardPaste || !backend.parseProxyString){
         showToast("main.py needs an update (add parseProxyString).");
         return;
      }

      backend.clipboardPaste(function(text){
        if(!text) return;

        backend.parseProxyString(text, function(jsonResult){
            let data = null;
            try { data = JSON.parse(jsonResult); } catch(e){}

            if(data && data.ok) {
                const mappedData = {
                    type: data.type,
                    host: data.host,
                    port: data.port,
                    user: data.username,
                    pass: data.password
                };

                applyProxyToForm(mappedData);
                showToast("Proxy pasted.");
            } else {
                showToast("Unable to recognize proxy format.");
            }
        });
      });
    });
  }

  const inpHost = el("epProxyHost");
  if(inpHost) {
      inpHost.addEventListener("paste", (e) => {
          e.preventDefault();

          let text = "";
          if (e.clipboardData && e.clipboardData.getData) {
              text = e.clipboardData.getData("text/plain");
          }

          if(!text) return;

          const parsed = smartParseProxy(text);

          if (parsed) {
              applyProxyToForm(parsed);
          } else {
              const start = inpHost.selectionStart;
              const end = inpHost.selectionEnd;
              const val = inpHost.value;

              inpHost.value = val.slice(0, start) + text + val.slice(end);

              inpHost.selectionStart = inpHost.selectionEnd = start + text.length;

              inpHost.dispatchEvent(new Event('input'));
          }
      });
  }

  const btnClean = el("epProxyClean");
  if(btnClean){
    btnClean.addEventListener("click", ()=>{
      if(el("epProxyHost")) el("epProxyHost").value = "";
      if(el("epProxyPort")) el("epProxyPort").value = "";
      if(el("epProxyUser")) el("epProxyUser").value = "";
      if(el("epProxyPass")) el("epProxyPass").value = "";

      const chk = el("epProxyEnabled");
      if(chk) {
          chk.checked = false;
          forceRepaintHard(chk);
      }

      resetProxyStatus();
      showToast("Proxy fields cleared.");
    });
  }

  const btnCopy = el("epProxyCopy");
  if(btnCopy){
    btnCopy.addEventListener("click", ()=>{
      const t = (el("epProxyType") ? el("epProxyType").value : "http").trim();
      const host = (el("epProxyHost") ? el("epProxyHost").value : "").trim();
      const port = (el("epProxyPort") ? el("epProxyPort").value : "").trim();
      const user = (el("epProxyUser") ? el("epProxyUser").value : "").trim();
      const pass = (el("epProxyPass") ? el("epProxyPass").value : "").trim();

      if(!host || !port){
        showToast("Nothing to copy.");
        return;
      }

      const auth = (user || pass) ? `${user}:${pass}@` : "";
      const s = `${t}://${auth}${host}:${port}`;

      if(backend && backend.clipboardCopy){
          backend.clipboardCopy(s);
          showToast("Copied to clipboard.");
      } else {
          showToast("Backend error.");
      }
    });
  }

  const btnCheck = el("epProxyCheck");
  if(btnCheck){
    btnCheck.addEventListener("click", ()=>{
      if(!backend || !backend.checkProxy){
        showToast("Backend does not implement checkProxy() yet. (Python patch required)");
        return;
      }

      const enabled = !!(el("epProxyEnabled") && el("epProxyEnabled").checked);
      const s = el("epProxyStatus");

      const host = (el("epProxyHost") ? el("epProxyHost").value : "").trim();
      const port = (el("epProxyPort") ? el("epProxyPort").value : "").trim();
      const user = (el("epProxyUser") ? el("epProxyUser").value : "").trim();
      const pass = (el("epProxyPass") ? el("epProxyPass").value : "").trim();

      if(!enabled){
        showToast("Please enable proxy to check.");
        if(s) s.classList.remove("is-live", "is-die");
        return;
      }

      if(!host || !port){
        showToast("Proxy information is missing. Please enter Host and Port.");
        if(s) s.classList.remove("is-live", "is-die");
        return;
      }

      if( (user && !pass) || (!user && pass) ){
        showToast("The proxy server information is incorrect. Try another proxy server.");
        if(s) s.classList.remove("is-live", "is-die");
        return;
      }

      const data = readEditForm();
      const proxy = (data && data.proxy) ? data.proxy : {};
      proxy.enabled = true;

      const reqId = `${Date.now()}_${Math.random().toString(16).slice(2)}`;
      state.proxyCheckReqId = reqId;

      if(s) {
          s.textContent = "Checking...";
          s.classList.add("is-live");
          s.classList.remove("is-die");
      }
      btnCheck.disabled = true;

      backend.checkProxy(reqId, JSON.stringify(proxy));
    });
  }

  const epCancel = el("epCancel");
  if(epCancel) epCancel.addEventListener("click", ()=>showProfilesView());

  const epSave = el("epSave");
  if(epSave){
    epSave.addEventListener("click", ()=>{
      const data = readEditForm();

      let qty = 1;
      const inpQty = el("epQty");
      if(inpQty) qty = parseInt(inpQty.value) || 1;

      if(state.isCreateMode){
        if(!backend || !backend.createProfileFull){
           showToast("Backend does not implement createProfileFull() yet.");
           return;
        }

        epSave.disabled = true;

        backend.createProfileFull(
            data.name,
            data.os,
            qty,
            data.notes,
            JSON.stringify(data.proxy),
            JSON.stringify(data.webrtc)
        );

        setTimeout(()=>{
            epSave.disabled = false;
            showProfilesView();
        }, 300);

      } else {
        if(!backend || !backend.saveProfileDetail){
          showToast("Backend error.");
          return;
        }
        const pid = state.editProfileId;
        if(!pid) return;
        if(!data.name){ showToast("Name is required."); return; }

        epSave.disabled = true;
        backend.saveProfileDetail(pid, JSON.stringify(data), function(ok){
          epSave.disabled = false;
          if(ok){
            showToast("Saved.");
            if(backend.refresh) backend.refresh();
            showProfilesView();
          }else{
            showToast("Save failed.");
          }
        });
      }
    });
  }

  function openLicense(){
    const ov = el("licOverlay");
    if(!ov) return;
    ov.classList.add("open");
    ov.setAttribute("aria-hidden", "false");
  }

  function closeLicense(){
    const ov = el("licOverlay");
    if(!ov) return;
    ov.classList.remove("open");
    ov.setAttribute("aria-hidden", "true");
  }

  const licLink = el("licLink");
  if(licLink){
    licLink.addEventListener("click", (e)=>{
      e.preventDefault();
      openLicense();
    });
  }

  const licClose = el("licClose");
  if(licClose) licClose.addEventListener("click", closeLicense);

  const licCloseX = el("licCloseX");
  if(licCloseX) licCloseX.addEventListener("click", closeLicense);

  const licOverlay = el("licOverlay");
  if(licOverlay){
    licOverlay.addEventListener("mousedown", (e)=>{
      if(e.target === licOverlay) closeLicense();
    });
  }

  document.addEventListener("keydown", (e)=>{
    if(e.key === "Escape"){
      const ov = el("licOverlay");
      if(ov && ov.classList.contains("open")) closeLicense();
    }
  });

  setupWebChannel();
});

const btnClose = document.getElementById("dlgClose");
const btnCancel = document.getElementById("dlgCancel");
const btnOk = document.getElementById("dlgOk");
const ov = document.getElementById("dlgOverlay");

if(btnClose) btnClose.addEventListener("click", closeDeleteDlg);
if(btnCancel) btnCancel.addEventListener("click", closeDeleteDlg);

if(ov){
  ov.addEventListener("mousedown", (e) => {
    if(e.target === ov) closeDeleteDlg();
  });
}

if(btnOk){
  btnOk.addEventListener("click", () => {
    if(!pendingDelete) return closeDeleteDlg();
    const p = pendingDelete;
    closeDeleteDlg();
    if(window.backend && window.backend.deleteProfile){
      window.backend.deleteProfile(p.id);
    } else if(typeof backend !== "undefined" && backend && backend.deleteProfile){
      backend.deleteProfile(p.id);
    }
  });
}

window.addEventListener("keydown", (e) => {
  if(e.key === "Escape"){
    const o = document.getElementById("dlgOverlay");
    if(o && o.classList.contains("open")) closeDeleteDlg();
  }
});

/* ============================================================
   SMART PROXY PARSER (FULL FORMAT SUPPORT)
   Supports ALL forms:
   1. 192.168.1.1:8000
   2. 192.168.1.1:8000:user:pass
   3. user:pass@192.168.1.1:8000

   4. With scheme (type) + ://
      - socks5://192.168.1.1:8000
      - socks5://192.168.1.1:8000:user:pass
      - socks5://user:pass@192.168.1.1:8000

   5. With type + space (additional format)
      - socks5 192.168.1.1:8000
      - socks5 192.168.1.1:8000:user:pass
      - http 192.168.1.1:8000
      - http user:pass@192.168.1.1:8000
   ============================================================ */

/* ===================== Proxy Utilities ===================== */

function smartParseProxy(text) {
  text = (text || "").trim();
  if (!text) return null;

  const regex = /(?:(http|https|socks4|socks5|socks5h)(?::\/\/|\s+))?(?:([^:@\s]+):([^:@\s]+)@)?([a-zA-Z0-9.-]+):(\d{1,5})(?::([^:\s]+):([^:\s]+))?/i;

  const match = text.match(regex);

  if (!match) return null;

  let rawType = (match[1] || "").toLowerCase();

  let type = "http";
  if (rawType.includes("socks4")) type = "socks4";
  else if (rawType.includes("socks5")) type = "socks5";
  else if (rawType.includes("http")) type = "http";

  let host = match[4];
  let port = match[5];

  let user = match[2] || match[6] || "";
  let pass = match[3] || match[7] || "";

  const portNum = parseInt(port, 10);
  if (isNaN(portNum) || portNum < 0 || portNum > 65535) return null;

  if (/^\.+$/.test(host)) return null;

  return { type, host, port, user, pass };
}

function applyProxyToForm(data) {
  if (!data) return;

  const chk = document.getElementById("epProxyEnabled");
  if(chk) {
      chk.checked = true;
      forceRepaintHard(chk);
  }

  const typeSel = document.getElementById("epProxyTypeSel");
  const typeInp = document.getElementById("epProxyType");

  if(data.type && ["http", "socks4", "socks5"].includes(data.type)) {
     if(typeof setCSelectValue === "function") setCSelectValue(typeSel, data.type);
     if(typeInp) typeInp.value = data.type;
     if(typeof updateProxyAuthEnabled === "function") updateProxyAuthEnabled();
  }

  if(document.getElementById("epProxyHost")) document.getElementById("epProxyHost").value = data.host;
  if(document.getElementById("epProxyPort")) document.getElementById("epProxyPort").value = data.port;

  const u = document.getElementById("epProxyUser");
  const p = document.getElementById("epProxyPass");
  if(u && !u.disabled) u.value = data.user;
  if(p && !p.disabled) p.value = data.pass;

  if(typeof resetProxyStatus === "function") resetProxyStatus();
}

/* ===================== Exit Dialog ===================== */
const exitOv = document.getElementById("exitOverlay");
const btnExitYes = document.getElementById("btnExitYes");
const btnExitNo = document.getElementById("btnExitNo");
const exitCloseX = document.getElementById("exitCloseX");

function openExitDlg(count) {
  if (exitOv) {
    exitOv.classList.add("open");
    exitOv.setAttribute("aria-hidden", "false");
  }
}

function closeExitDlg() {
  if (exitOv) {
    exitOv.classList.remove("open");
    exitOv.setAttribute("aria-hidden", "true");
  }
}

if (btnExitYes) {
  btnExitYes.addEventListener("click", () => {
    closeExitDlg();
    if (backend && backend.confirmExit) {
      backend.confirmExit(true);
    }
  });
}

if (btnExitNo) {
  btnExitNo.addEventListener("click", () => {
    closeExitDlg();
    if (backend && backend.confirmExit) {
      backend.confirmExit(false);
    }
  });
}

if (exitCloseX) exitCloseX.addEventListener("click", closeExitDlg);

if (exitOv) {
  exitOv.addEventListener("mousedown", (e) => {
    if (e.target === exitOv) closeExitDlg();
  });
}
# ============================================================================
# IMPORTS
# ============================================================================

import json
import random
import shutil
import base64
from pathlib import Path

# ============================================================================
# CONSTANTS & DATABASES
# ============================================================================

WIN_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
]

MAC_UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Arm Mac OS X 14_2_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

WEBGL_WIN_POOL = [
    {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (NVIDIA)", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 2060 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (AMD)", "renderer": "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)"},
    {"vendor": "Google Inc. (AMD)", "renderer": "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)"},
]

WEBGL_MAC_POOL = [
    {"vendor": "Google Inc. (Apple)", "renderer": "ANGLE (Apple, Apple M1, OpenGL 4.1)"},
    {"vendor": "Google Inc. (Apple)", "renderer": "ANGLE (Apple, Apple M1 Pro, OpenGL 4.1)"},
    {"vendor": "Google Inc. (Apple)", "renderer": "ANGLE (Apple, Apple M2, OpenGL 4.1)"},
    {"vendor": "Google Inc. (Apple)", "renderer": "ANGLE (Apple, Apple M2 Pro, OpenGL 4.1)"},
    {"vendor": "Google Inc. (Apple)", "renderer": "ANGLE (Apple, Apple M3, OpenGL 4.1)"},
]

SCREEN_RESOLUTIONS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 2560, "height": 1440},
]

# ============================================================================
# FINGERPRINT CONFIG GENERATION
# ============================================================================

def generate_fingerprint(os_type: str):
    is_mac = "mac" in os_type.lower()
    
    # 1. User Agent & Platform
    if is_mac:
        ua = random.choice(MAC_UAS)
        platform = "MacIntel"
        webgl_pool = WEBGL_MAC_POOL
    else:
        ua = random.choice(WIN_UAS)
        platform = "Win32"
        webgl_pool = WEBGL_WIN_POOL

    # 2. Canvas Noise Config - Tăng entropy
    def _nz(): return random.choice([-3, -2, -1, 1, 2, 3])
    
    canvas_noise = {
        "salt": random.randint(100000, 999999),
        "r": _nz(), 
        "g": _nz(), 
        "b": _nz(),
        # Thêm các tham số để tạo unique signature
        "variant": random.randint(1, 100),  # Biến thể
        "multiplier": random.uniform(0.8, 1.2)  # Hệ số nhân
    }

    # [PATCH 1] Audio Noise: Tăng biên độ mạnh (0.001 - 0.01) để fix trùng Iphey
    audio_noise = random.uniform(0.001, 0.01)

    # 4. Screen Resolution
    screen = random.choice(SCREEN_RESOLUTIONS)
    # availHeight thường nhỏ hơn height do thanh taskbar (Win) hoặc dock (Mac)
    avail_diff = random.randint(30, 60)
    screen_conf = {
        "width": screen["width"],
        "height": screen["height"],
        "availHeight": screen["height"] - avail_diff,
        "availWidth": screen["width"],
        "colorDepth": 24,
        "pixelDepth": 24
    }

    # 5. Hardware Concurrency & Memory
    cores = random.choice([4, 8, 12, 16])
    ram = random.choice([4, 8, 16, 32])

    # [PATCH 1] Tách version động từ chuỗi UA (Không hardcode)
    import re
    ua_major = "120" # Giá trị mặc định an toàn
    match = re.search(r"Chrome/(\d+)", ua)
    if match:
        ua_major = match.group(1)

    nav_config = {
        "userAgent": ua,
        "uaVersion": ua_major, # Truyền version chính xác xuống JS
        "platform": platform,
        "hardwareConcurrency": cores,
        "deviceMemory": ram,
        "maxTouchPoints": 0,
        "webdriver": False,
        "appVersion": ua.replace("Mozilla/", "")
    }
        
    return {
        "userAgent": ua,
        "canvasNoise": canvas_noise,
        "webgl": random.choice(webgl_pool),
        "audioNoise": random.uniform(0.0001, 0.0005), # [PATCH 2] Tăng noise để khác Hash
        "screen": screen_conf,
        "navigator": nav_config,
        "clientRectsNoise": random.uniform(0.2, 1.2), # [PATCH 2] Tăng noise Rects mạnh hơn
    }

# ============================================================================
# EXTENSION BUILD HELPERS
# ============================================================================

def _save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def append_load_extension_arg(args: list, ext_dir: Path) -> None:
    import os
    abs_path = os.path.abspath(str(ext_dir))
    
    # Debug
    print(f"DEBUG PATH: {abs_path}")
    
    # Xóa flag cũ
    args[:] = [a for a in args if not str(a).startswith("--load-extension=")]
    args[:] = [a for a in args if not str(a).startswith("--disable-extensions-except=")]

    # QUAN TRỌNG: Không dùng disable-extensions-except nữa (vì đã sạch policy)
    # Chỉ dùng load-extension
    args.append(f"--load-extension={abs_path}")

# ============================================================================
# EXTENSION BUILD PIPELINE
# ============================================================================

def build_profile_extension(ext_path: Path, fingerprint: dict, webrtc_mode: str, 
                          timezone: str = "", public_ip: str = "", 
                          extra_scripts: dict = None) -> Path:
    if ext_path.exists(): shutil.rmtree(ext_path, ignore_errors=True)
    ext_path.mkdir(parents=True, exist_ok=True)
    

    reload_code = r"""
    (function() {
        try {
            const key = "smcd_loaded_v1";
            if (!sessionStorage.getItem(key)) {
                sessionStorage.setItem(key, "1");
                
                // Ngắt ngay lập tức việc tải trang hiện tại (tránh lộ IP gốc nếu proxy chưa kịp)
                window.stop();
                
                // Reload lại trang
                // setTimeout 100ms để đảm bảo trình duyệt kịp khởi tạo network stack
                setTimeout(() => {
                    window.location.reload();
                }, 100);
            }
        } catch(e) {}
    })();
    """
    (ext_path / "reload.js").write_text(reload_code, encoding="utf-8")

    spoofer_code = _build_spoofer_code(fingerprint, webrtc_mode, timezone, public_ip)
    (ext_path / "spoofer.js").write_text(spoofer_code, encoding="utf-8")
    

    policy_val = 'disable_non_proxied_udp' if public_ip else 'default'
    bg_code = f"""
    try {{
        if (chrome.privacy && chrome.privacy.network && chrome.privacy.network.webRTCIPHandlingPolicy) {{
            chrome.privacy.network.webRTCIPHandlingPolicy.set({{
                value: '{policy_val}'
            }});
        }}
    }} catch(e) {{ console.error("BG Policy Error", e); }}
    """
    (ext_path / "background.js").write_text(bg_code, encoding="utf-8")

    js_files = ["reload.js", "spoofer.js"]
    
    if extra_scripts:
        for filename, content in extra_scripts.items():
            (ext_path / filename).write_text(content, encoding="utf-8")
            js_files.append(filename)

    manifest = {
        "manifest_version": 3,
        "name": "Offline Browser Profile V31.2",
        "version": "31.2.0",
        "permissions": ["privacy", "declarativeNetRequest", "storage", "scripting"],
        "host_permissions": ["<all_urls>"],
        "background": {"service_worker": "background.js", "type": "module"},
        "content_scripts": [
            {
                "matches": ["<all_urls>"],
                "js": js_files,
                "run_at": "document_start",
                "all_frames": True,
                "match_about_blank": True,
                "world": "MAIN"
            }
        ]
    }
    _save_json(ext_path / "manifest.json", manifest)
    return ext_path

# ============================================================================
# CONTENT SCRIPT SPOOFER GENERATION
# ============================================================================

def _build_spoofer_code(fp: dict, webrtc_mode: str, timezone: str, public_ip: str = "") -> str:

    wm = (webrtc_mode or "altered").strip().lower()
    if wm not in ("altered", "disabled"): wm = "altered"

    config_payload = {
        "WEBRTC_MODE": wm,
        "PUBLIC_IP": public_ip,
        "TZ": timezone or "",
        "CANVAS": fp.get("canvasNoise", {}),
        "WEBGL": fp.get("webgl", {}),
        "NAV": None,
        "AUDIO": fp.get("audioNoise", 0.0000001),
        "RECTS": fp.get("clientRectsNoise", 0),
    }

    json_str = json.dumps(config_payload)
    b64_config = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
    
    return r"""(function() {
    'use strict';
    try {
        const B64_CONFIG = "___B64_CONFIG___";
        const CONFIG = JSON.parse(atob(B64_CONFIG));
        
        // [PATCH] Protect Function Upgrade: Hỗ trợ Getter để Fix lỗi Iphey Red
        const protect = (fn, name, isGetter = false) => {
            const str = isGetter ? "function get " + name + "() { [native code] }" : "function " + name + "() { [native code] }";
            try { Object.defineProperty(fn, "name", { value: name }); } catch(e) {}
            try { 
                Object.defineProperty(fn, "toString", { 
                    value: function() { return str; },
                    configurable: true, writable: true 
                }); 
            } catch(e) {}
            return fn;
        };

        if (window.RTCPeerConnection) {
            const OrigRPC = window.RTCPeerConnection;

            // =================================================================
            // CASE A: DISABLED (Dùng Native Wrapper V47 để triệt tiêu kết nối)
            // =================================================================
            if (CONFIG.WEBRTC_MODE === 'disabled') {
                const spoofer = function(config, ...args) {
                    const newConfig = config || {};
                    // Cắt server và ép relay -> không thể kết nối
                    newConfig.iceServers = [];
                    newConfig.iceTransportPolicy = 'relay';

                    const pc = new OrigRPC(newConfig, ...args);

                    // Chặn sự kiện candidate
                    const origAddEL = pc.addEventListener;
                    pc.addEventListener = function(type, listener, options) {
                        if (type === 'icecandidate') {
                            const wrapped = (e) => {
                                if (e.candidate) return; // Chặn candidate
                                listener(e); // Cho phép null (kết thúc)
                            };
                            return origAddEL.call(this, type, wrapped, options);
                        }
                        return origAddEL.call(this, type, listener, options);
                    };
                    
                    // Hook onicecandidate
                    let _onicecandidate = null;
                    Object.defineProperty(pc, 'onicecandidate', {
                        get: () => _onicecandidate,
                        set: (cb) => { _onicecandidate = cb; },
                        configurable: true
                    });
                    
                    // Hook dispatchEvent
                    const origDispatch = pc.dispatchEvent;
                    pc.dispatchEvent = function(e) {
                        if (e.type === 'icecandidate' && _onicecandidate) {
                            if (e.candidate) return;
                            _onicecandidate(e);
                        }
                        return origDispatch.call(this, e);
                    };

                    return pc;
                };
                spoofer.prototype = OrigRPC.prototype;

                // Hook CreateOffer để xóa SDP 
                const origCreateOffer = OrigRPC.prototype.createOffer;
                OrigRPC.prototype.createOffer = function(options) {
                    return origCreateOffer.call(this, options).then(offer => {
                        if (offer && offer.sdp) {
                            let sdp = offer.sdp;
                            sdp = sdp.replace(/c=IN IP4 .*/g, "c=IN IP4 0.0.0.0");
                            const lines = sdp.split('\r\n');
                            const filtered = lines.filter(line => !line.includes('a=candidate'));
                            offer.sdp = filtered.join('\r\n');
                        }
                        return offer;
                    });
                };
                
                Object.defineProperty(window, "RTCPeerConnection", { 
                    value: spoofer, configurable: true, writable: true 
                });
                protect(window.RTCPeerConnection, "RTCPeerConnection");
            }

            // =================================================================
            // CASE B: ALTERED + CÓ PROXY (Logic V31.1: Native Wrapper + Inject)
            // =================================================================
            else if (CONFIG.WEBRTC_MODE === 'altered' && CONFIG.PUBLIC_IP) {
                // Helpers của V31.1
                const getFakeCandidateString = (port) => {
                    const p = port || Math.floor(Math.random() * (60000 - 10000 + 1)) + 10000;
                    return `a=candidate:392746612 1 udp 1677729535 ${CONFIG.PUBLIC_IP} ${p} typ srflx raddr 0.0.0.0 rport 0 generation 0 network-cost 999`;
                };

                const getFakeCandidateObj = () => {
                    const sdp = getFakeCandidateString().replace("a=", "");
                    return new RTCIceCandidate({ candidate: sdp, sdpMid: "0", sdpMLineIndex: 0 });
                };

                const spoofer = function(config, ...args) {
                    const newConfig = config || {};
                    newConfig.iceServers = []; // V31.1: Xóa server thật
                    newConfig.iceTransportPolicy = 'relay'; // V31.1: Ép relay
                    
                    const pc = new OrigRPC(newConfig, ...args);

                    // V31.1: Mock onicecandidate
                    let _onicecandidate = null;
                    Object.defineProperty(pc, 'onicecandidate', {
                        get: () => _onicecandidate,
                        set: (cb) => { _onicecandidate = cb; },
                        configurable: true
                    });
                    
                    // V31.1: Chặn listener icecandidate hoàn toàn
                    const origAddEL = pc.addEventListener;
                    pc.addEventListener = function(type, listener, options) {
                        if (type === 'icecandidate') return; 
                        return origAddEL.call(this, type, listener, options);
                    };

                    // V31.1: Mock Properties & GetStats
                    let _fakeLocalDesc = null;
                    let _isClosed = false;
                    const descProp = { get: () => _fakeLocalDesc || null, configurable: true };
                    Object.defineProperties(pc, {
                        "localDescription": descProp,
                        "currentLocalDescription": descProp,
                        "pendingLocalDescription": descProp,
                        "iceConnectionState": { get: () => _isClosed ? "closed" : (_fakeLocalDesc ? "connected" : "new"), configurable: true },
                        "iceGatheringState": { get: () => _isClosed ? "complete" : (_fakeLocalDesc ? "complete" : "new"), configurable: true },
                        "signalingState": { get: () => _isClosed ? "closed" : (_fakeLocalDesc ? "stable" : "stable"), configurable: true }
                    });

                    const origGetStats = pc.getStats;
                    pc.getStats = function(selector) {
                        return new Promise((resolve, reject) => {
                            if (_fakeLocalDesc) {
                                const stats = new Map();
                                const ts = Date.now();
                                stats.set("candidate-pair", {
                                    id: "candidate-pair", timestamp: ts, type: "candidate-pair",
                                    state: "succeeded", writable: true, nominated: true,
                                    priority: 1677729535
                                });
                                resolve(stats);
                            } else {
                                origGetStats.apply(this, arguments).then(resolve).catch(reject);
                            }
                        });
                    };

                    // V31.1: Fire Fake Events
                    const fire = (name) => {
                        if (_isClosed) return;
                        try {
                            const e = new Event(name);
                            pc.dispatchEvent(e);
                            if (typeof pc['on' + name] === 'function') pc['on' + name](e);
                        } catch(err) {}
                    };

                    const origSetLocal = pc.setLocalDescription;
                    pc.setLocalDescription = function(desc) {
                        _fakeLocalDesc = desc;
                        
                        Promise.resolve().then(() => {
                            fire('signalingstatechange');
                            fire('icegatheringstatechange');
                            
                            // BẮN DUY NHẤT CANDIDATE FAKE
                            const candObj = getFakeCandidateObj();
                            if (candObj) {
                                try {
                                    const e = new Event('icecandidate');
                                    Object.defineProperty(e, 'candidate', { value: candObj, writable: false });
                                    pc.dispatchEvent(e);
                                    if (typeof _onicecandidate === 'function') _onicecandidate(e);
                                } catch(e){}
                            }
                        });

                        setTimeout(() => {
                            fire('icegatheringstatechange');
                            fire('iceconnectionstatechange');
                        }, 50);

                        return origSetLocal.call(this, desc).catch(e => Promise.resolve()); 
                    };
                    const origClose = pc.close;
                    pc.close = function() { _isClosed = true; return origClose.apply(this, arguments); };
                    return pc;
                };
                spoofer.prototype = OrigRPC.prototype;

                // V31.1: Create Offer Hook (Regex Injection)
                const origCreateOffer = OrigRPC.prototype.createOffer;
                OrigRPC.prototype.createOffer = function(options) {
                    return origCreateOffer.call(this, options).then(offer => {
                        if (offer && offer.sdp) {
                            let sdp = offer.sdp;
                            // 1. Fake Connection Line
                            const fakeCLine = `c=IN IP4 ${CONFIG.PUBLIC_IP}`;
                            sdp = sdp.replace(/c=IN IP4 0\.0\.0\.0/g, fakeCLine);
                            
                            // 2. Inject Fake Candidate via Regex
                            const regex = /(a=mid:(\w+))/g;
                            sdp = sdp.replace(regex, (match, p1, midVal) => {
                                const port = Math.floor(Math.random() * (60000 - 10000 + 1)) + 10000;
                                const candStr = getFakeCandidateString(port);
                                return `${p1}\r\n${candStr}`;
                            });
                            
                            // 3. Xóa sạch IP nội bộ
                            const lines = sdp.split('\r\n');
                            const filtered = lines.filter(line => {
                                if (line.includes('a=candidate')) {
                                    if (line.includes('typ host')) return false; 
                                    if (line.includes('192.168.') || line.includes('10.') || line.includes('172.') || line.includes('.local')) return false;
                                }
                                return true;
                            });
                            offer.sdp = filtered.join('\r\n');
                        }
                        return offer;
                    });
                };
                
                Object.defineProperty(window, "RTCPeerConnection", { 
                    value: spoofer, configurable: true, writable: true 
                });
                protect(window.RTCPeerConnection, "RTCPeerConnection");
            }

            // =================================================================
            // CASE C: ALTERED + NO PROXY
            // (Không làm gì cả -> Giống trình duyệt thường -> Đúng yêu cầu)
            // =================================================================
        }
        
        // =================================================================
        // NAVIGATOR & CLIENT HINTS (FIX PHáº M VI BIáº¾N & PROTOTYPE)
        // =================================================================
        if (CONFIG.NAV) {
            try {
                // 1. Setup dá»¯ liá»‡u Fake
                const uaVersion = CONFIG.NAV.uaVersion || "120";
                const fullVersion = uaVersion + ".0.0.0"; 
                const platform = CONFIG.NAV.platform || "Win32";
                
                // FIX 1: Brands pháº£i match chÃ­nh xÃ¡c vá»›i Chrome hiá»‡n táº¡i
                const brands = [
                    {brand: "Not A(Brand", version: "8"},  // LÆ°u Ã½: dáº¥u ( thay vÃ¬ _
                    {brand: "Chromium", version: uaVersion},
                    {brand: "Google Chrome", version: uaVersion}
                ];
        
                // 2. Override thuá»™c tÃ­nh cÆ¡ báº£n
                const override = (prop, val) => {
                    if (prop in navigator) {
                        Object.defineProperty(navigator, prop, { 
                            get: protect(() => val, prop, true),
                            configurable: true,
                            enumerable: true
                        });
                    }
                };
                
                if (CONFIG.NAV.userAgent) override('userAgent', CONFIG.NAV.userAgent);
                if (CONFIG.NAV.appVersion) override('appVersion', CONFIG.NAV.appVersion);
                if (CONFIG.NAV.platform) override('platform', platform);
                if (CONFIG.NAV.hardwareConcurrency) override('hardwareConcurrency', CONFIG.NAV.hardwareConcurrency);
                if (CONFIG.NAV.deviceMemory) override('deviceMemory', CONFIG.NAV.deviceMemory);
                
                override('webdriver', false);
                override('maxTouchPoints', 0);
                override('vendor', 'Google Inc.');
                override('language', 'en-US');
                override('languages', ['en-US', 'en']);
                override('onLine', true);
                override('cookieEnabled', true);
                override('doNotTrack', null);
                override('pdfViewerEnabled', true);
                
                // FIX 2: Plugins vÃ  MimeTypes
                Object.defineProperty(navigator, 'plugins', {
                    get: protect(() => ({
                        length: 5,
                        item: (i) => null,
                        namedItem: (n) => null,
                        refresh: () => {},
                        [Symbol.iterator]: function* () {}
                    }), 'plugins', true),
                    configurable: true,
                    enumerable: true
                });
                
                Object.defineProperty(navigator, 'mimeTypes', {
                    get: protect(() => ({
                        length: 4,
                        item: (i) => null,
                        namedItem: (n) => null,
                        [Symbol.iterator]: function* () {}
                    }), 'mimeTypes', true),
                    configurable: true,
                    enumerable: true
                });
        
                // FIX 3: Client Hints - QUAN TRá»ŒNG NHáº¤T
                if (Navigator.prototype) {
                    const platformVersion = platform === "Win32" ? "10.0.0" : "15.0.0";
                    const architecture = "x86";
                    const bitness = "64";
                    const model = "";
                    const mobile = false;
                    
                    const uaDataGetter = function() {
                        return {
                            brands: brands,
                            mobile: mobile,
                            platform: platform,
                            
                            // FIX: getHighEntropyValues pháº£i trung thá»±c vá»›i spec
                            getHighEntropyValues: protect(function(hints) {
                                return Promise.resolve().then(() => {
                                    const result = {
                                        brands: brands,
                                        mobile: mobile,
                                        platform: platform
                                    };
                                    
                                    // Cung cáº¥p Ä'áº§y Ä'á»§ hints nháº­n Ä'Æ°á»£c
                                    const hintsArray = Array.isArray(hints) ? hints : [];
                                    
                                    if (hintsArray.includes("platformVersion")) {
                                        result.platformVersion = platformVersion;
                                    }
                                    if (hintsArray.includes("architecture")) {
                                        result.architecture = architecture;
                                    }
                                    if (hintsArray.includes("bitness")) {
                                        result.bitness = bitness;
                                    }
                                    if (hintsArray.includes("model")) {
                                        result.model = model;
                                    }
                                    if (hintsArray.includes("uaFullVersion")) {
                                        result.uaFullVersion = fullVersion;
                                    }
                                    if (hintsArray.includes("fullVersionList")) {
                                        result.fullVersionList = [
                                            {brand: "Not A(Brand", version: "8.0.0.0"},
                                            {brand: "Chromium", version: fullVersion},
                                            {brand: "Google Chrome", version: fullVersion}
                                        ];
                                    }
                                    
                                    return result;
                                });
                            }, "getHighEntropyValues"),
                            
                            // FIX: ThÃªm toJSON Ä'á»ƒ serialize Ä'Ãºng
                            toJSON: protect(function() {
                                return {
                                    brands: brands,
                                    mobile: mobile,
                                    platform: platform
                                };
                            }, "toJSON")
                        };
                    };
                    
                    Object.defineProperty(Navigator.prototype, 'userAgentData', {
                        get: protect(uaDataGetter, 'userAgentData', true),
                        configurable: true,
                        enumerable: true
                    });
                }
                
            } catch(e) { console.error("Nav Spoof Error", e); }
        }

        // ==========================================
        // CÁC SPOOFER PHỤ (GIỮ NGUYÊN V47)
        // ==========================================
        if (CONFIG.TZ) {
            const OriginalDTF = Intl.DateTimeFormat;
            const _origToLocaleString = Date.prototype.toLocaleString;
            const ProxiedDTF = function(locales, options) {
                const opts = options ? Object.assign({}, options) : {};
                opts.timeZone = CONFIG.TZ;
                return new OriginalDTF(locales, opts);
            };
            ProxiedDTF.prototype = OriginalDTF.prototype;
            ProxiedDTF.supportedLocalesOf = OriginalDTF.supportedLocalesOf;
            protect(ProxiedDTF, "DateTimeFormat");
            Intl.DateTimeFormat = ProxiedDTF;
            const origResolved = OriginalDTF.prototype.resolvedOptions;
            OriginalDTF.prototype.resolvedOptions = function() {
                const o = origResolved.call(this);
                o.timeZone = CONFIG.TZ;
                return o;
            };
            const getSpoofedString = function() {
                try {
                    const dtf = new OriginalDTF("en-US", {
                        timeZone: CONFIG.TZ,
                        weekday: "short", month: "short", day: "2-digit", year: "numeric",
                        hour: "2-digit", minute: "2-digit", second: "2-digit",
                        hour12: false, timeZoneName: "short"
                    });
                    const p = dtf.formatToParts(this).reduce((a, v) => { a[v.type] = v.value; return a; }, {});
                    const dtfOff = new OriginalDTF("en-US", { timeZone: CONFIG.TZ, timeZoneName: "longOffset" });
                    const offPart = dtfOff.formatToParts(this).find(x => x.type === "timeZoneName");
                    const gmt = "GMT" + (offPart ? offPart.value.replace("GMT", "").replace(":", "") : "+0000");
                    const dtfLong = new OriginalDTF("en-US", { timeZone: CONFIG.TZ, timeZoneName: "long" });
                    const tzName = dtfLong.formatToParts(this).find(x => x.type === "timeZoneName")?.value || CONFIG.TZ;
                    return `${p.weekday} ${p.month} ${p.day} ${p.year} ${p.hour}:${p.minute}:${p.second} ${gmt} (${tzName})`;
                } catch(e) { return this.toUTCString(); }
            };
            Date.prototype.toString = getSpoofedString;
            protect(Date.prototype.toString, "toString");
            Date.prototype.toLocaleString = function(locales, options) {
                const opts = options ? Object.assign({}, options) : {};
                opts.timeZone = CONFIG.TZ; 
                return _origToLocaleString.call(this, locales, opts);
            };
            protect(Date.prototype.toLocaleString, "toLocaleString");
        }

        // =================================================================
        // CANVAS FIX: BLOCK AT EXIT (OFFSET STRATEGY)
        // =================================================================
        try {
            const salt = CONFIG.CANVAS.salt || 333;
            const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
            
            HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
                // 1. Chỉ can thiệp các canvas có nội dung (>16px)
                // Điều này tránh ảnh hưởng các icon hoặc canvas kỹ thuật nhỏ
                if (this.width > 16 && this.height > 16) {
                    try {
                        const shadow = document.createElement('canvas');
                        shadow.width = this.width;
                        shadow.height = this.height;
                        const ctx = shadow.getContext("2d");
                        
                        // 2. KỸ THUẬT: DỊCH CHUYỂN KHUNG HÌNH (FRAME SHIFT)
                        // Thay vì vẽ tại (0,0), ta vẽ lệch đi một khoảng siêu nhỏ (0.01px - 0.1px)
                        // Điều này buộc trình duyệt phải tính toán lại (Anti-aliasing) toàn bộ bức ảnh
                        // -> Hash thay đổi 100% nhưng mắt thường không thấy khác biệt
                        const shift = (salt % 10) * 0.01 + 0.02; 
                        
                        ctx.drawImage(this, shift, shift);
                        
                        // 3. NHIỄU BỔ SUNG: Vẽ 1 điểm ảnh mờ ở góc để chắc chắn
                        // Tránh trường hợp ảnh quá đơn giản (nền trắng) khiến Shift không tác dụng
                        const noiseColor = (salt % 255);
                        ctx.fillStyle = "rgba(" + noiseColor + ", " + (255 - noiseColor) + ", 100, 0.01)";
                        ctx.fillRect(0, 0, 1, 1);
                        
                        // Trả về dữ liệu từ Shadow Canvas
                        return origToDataURL.call(shadow, type, quality);
                    } catch(e) {
                        // Nếu có lỗi (ví dụ Tainted Canvas), fallback về gốc
                    }
                }
                return origToDataURL.call(this, type, quality);
            };
            protect(HTMLCanvasElement.prototype.toDataURL, "toDataURL");
            
            const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
                CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {
                    const data = origGetImageData.call(this, x, y, w, h);
                    if (w > 16 && h > 16) { 
                         // Làm nhiễu nhẹ mảng pixel để đổi Hash
                         const s = (CONFIG.CANVAS.salt || 333) % 7;
                         for (let i = 0; i < data.data.length; i += 40) {
                             data.data[i] = data.data[i] ^ s;
                         }
                    }
                    return data;
                };
                protect(CanvasRenderingContext2D.prototype.getImageData, "getImageData");

        } catch(e) { console.error("Canvas Patch Error", e); }

        // =================================================================
        // FIX PATCH 2.1: WEBGL SPOOFING (SAFE MODE)
        // Sửa lỗi BrowserLeaks trống & Pixelscan treo
        // =================================================================
        if (CONFIG.WEBGL) {
            try {
                // Helper để hook an toàn, không crash nếu web gọi sai context
                const safeOverride = (proto) => {
                    const origGetParameter = proto.getParameter;
                    
                    // Ghi đè bằng Proxy hoặc Wrap function để giữ 'this' context chuẩn
                    proto.getParameter = function(parameter) {
                        // 37445: UNMASKED_VENDOR_WEBGL
                        // 37446: UNMASKED_RENDERER_WEBGL
                        if (parameter === 37445) return CONFIG.WEBGL.vendor;
                        if (parameter === 37446) return CONFIG.WEBGL.renderer;
                        
                        try {
                            return origGetParameter.apply(this, arguments);
                        } catch(e) {
                            // Nếu lỗi, thử gọi trực tiếp (fallback) để tránh crash trang web
                            return null;
                        }
                    };
                    protect(proto.getParameter, "getParameter");
                };

                // Hook WebGL 1
                if (window.WebGLRenderingContext) {
                    safeOverride(window.WebGLRenderingContext.prototype);
                }

                // Hook WebGL 2 (Quan trọng cho browser đời mới)
                if (window.WebGL2RenderingContext) {
                    safeOverride(window.WebGL2RenderingContext.prototype);
                }
                
                // [FIX] Tăng entropy cho WebGL (Tránh trùng lặp 100%)
                const spoofReadPixels = (proto) => {
                    const origRead = proto.readPixels;
                    proto.readPixels = function(x, y, width, height, format, type, pixels) {
                        const res = origRead.apply(this, arguments);
                        try {
                            if (pixels && pixels.length > 0) {
                                const salt = CONFIG.CANVAS.salt || 9999;
                                // Dùng toàn bộ giá trị salt để tính noise (biên độ rộng hơn)
                                // Thay vì chỉ có 7 biến thể, giờ sẽ có 255 biến thể
                                const noise = (salt % 255) + 1; 
                                
                                pixels[0] = pixels[0] ^ noise;
                                // Tăng mật độ nhiễu
                                for (let i = 20; i < pixels.length; i += 37) {
                                     pixels[i] = pixels[i] ^ noise;
                                }
                            }
                        } catch(e) {}
                        return res;
                    };
                    protect(proto.readPixels, "readPixels");
                };
                
                // Áp dụng cho cả WebGL 1 và 2
                if (window.WebGLRenderingContext) spoofReadPixels(window.WebGLRenderingContext.prototype);
                if (window.WebGL2RenderingContext) spoofReadPixels(window.WebGL2RenderingContext.prototype);
            
            } catch(e) { console.error("WebGL Spoof Error", e); }
        }
        
        // =================================================================
        // AUDIO FIX: BUFFER DATA HOOK (LOW LEVEL)
        // =================================================================
        if (CONFIG.AUDIO) {
            try {
                // Hook trực tiếp vào nơi chứa dữ liệu âm thanh
                // Bất kể OfflineAudioContext hay AudioContext đều phải qua đây
                if (window.AudioBuffer && window.AudioBuffer.prototype) {
                    const origGetChannelData = window.AudioBuffer.prototype.getChannelData;
                    
                    window.AudioBuffer.prototype.getChannelData = function(channel) {
                        const data = origGetChannelData.call(this, channel);
                        
                        // Nếu data đã bị làm nhiễu (đánh dấu) thì bỏ qua để tránh cộng dồn
                        if (data._spoofed) return data;
                        
                        // Seeded Random đơn giản để đảm bảo tính nhất quán (Iphey Xanh)
                        let seed = CONFIG.CANVAS.salt || 12345;
                        const random = () => {
                            seed = (seed * 9301 + 49297) % 233280;
                            return seed / 233280;
                        };

                        // Rải nhiễu biên độ lớn hơn (1e-4)
                        for (let i = 0; i < data.length; i += 50) {
                            const noise = (random() * 0.0002) - 0.0001;
                            data[i] += noise;
                        }
                        
                        // Đánh dấu đã xử lý
                        Object.defineProperty(data, '_spoofed', { value: true, enumerable: false });
                        
                        return data;
                    };
                    protect(window.AudioBuffer.prototype.getChannelData, "getChannelData");
                }
            } catch(e) { console.error("Audio Patch Error", e); }
        }

        // --- RECTS PATCH (SCALING) ---
        if (CONFIG.RECTS) {
             try {
                 // Dùng tỷ lệ scale thay vì cộng số cố định
                 // CONFIG.RECTS từ Python (0.2 - 1.2) * hệ số nhỏ
                 const rectScale = 1.0 + (CONFIG.RECTS * 0.00001); 
                 
                 const spoofRect = (r) => {
                     if (!r) return r;
                     return {
                         x: r.x, y: r.y, top: r.top, bottom: r.bottom, left: r.left, right: r.right,
                         width: r.width * rectScale, 
                         height: r.height * rectScale,
                         toJSON: function() { return this; }
                     };
                 };

                 const origRect = Element.prototype.getBoundingClientRect;
                 Element.prototype.getBoundingClientRect = function() {
                     return spoofRect(origRect.apply(this, arguments));
                 };
                 protect(Element.prototype.getBoundingClientRect, "getBoundingClientRect");

                 const origRects = Element.prototype.getClientRects;
                 Element.prototype.getClientRects = function() {
                     const rects = origRects.apply(this, arguments);
                     const fake = [];
                     for(let i=0; i<rects.length; i++) {
                         fake.push(spoofRect(rects[i]));
                     }
                     return fake;
                 };
                 protect(Element.prototype.getClientRects, "getClientRects");

             } catch(e) {}
        }

    } catch (e) { console.error("Spoof Init Error", e); }
})();""".replace("___B64_CONFIG___", b64_config)
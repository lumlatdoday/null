def get_badge_script(profile_name: str) -> str:
    safe_name = (profile_name or "Unknown").replace('"', '\\"').replace("'", "\\'")
    
    return f"""
(function() {{
    const P_NAME = "{safe_name}";
    const COLOR_TEAL = "#18c7b6";
    
    try {{
        if (window.top === window.self) {{
            
            // ==========================================
            // 1. UI BADGE
            // ==========================================
            const d = document.createElement('div');
            d.innerText = P_NAME;
            d.style.cssText = `
                position: fixed; 
                bottom: 8px; 
                right: 8px; 
                padding: 4px 10px; 
                
                border: 1px solid ${{COLOR_TEAL}}; 
                color: ${{COLOR_TEAL}}; 
                background: rgba(0,0,0,1); 
                
                opacity: 0.5; 
                
                z-index: 2147483647; 
                font-family: sans-serif; 
                font-weight: 400; 
                font-size: 13px; 
                line-height: 1.4;
                pointer-events: none; 
                border-radius: 4px;
                backdrop-filter: blur(2px);
            `;
            
            const appendUI = () => {{
                if(!document.body) return;
                const old = document.getElementById('smcd-badge');
                if(old) old.remove();
                d.id = 'smcd-badge';
                document.body.appendChild(d);
            }};

            // ==========================================
            // 2. TASKBAR / TAB TITLE MANAGER
            // ==========================================
            const prefix = P_NAME + " - ";
            
            const enforceTitle = () => {{
                if (document.title && !document.title.startsWith(prefix)) {{
                    document.title = prefix + document.title;
                }}
            }};

            const titleObserver = new MutationObserver(() => {{
                enforceTitle();
            }});
            
            const initTitleHook = () => {{
                const target = document.querySelector('title');
                if (target) {{
                    titleObserver.observe(target, {{ childList: true, characterData: true, subtree: true }});
                    enforceTitle();
                }} else {{
                    const headObserver = new MutationObserver(() => {{
                        const t = document.querySelector('title');
                        if (t) {{
                            headObserver.disconnect();
                            titleObserver.observe(t, {{ childList: true, characterData: true, subtree: true }});
                            enforceTitle();
                        }}
                    }});
                    if(document.head) headObserver.observe(document.head, {{ childList: true }});
                }}
            }};

            // ==========================================
            // INIT
            // ==========================================
            if(document.readyState === 'loading') {{
                document.addEventListener('DOMContentLoaded', () => {{
                    appendUI();
                    initTitleHook();
                }});
            }} else {{
                appendUI();
                initTitleHook();
            }}
        }}
    }} catch(e) {{}}
}})();
"""
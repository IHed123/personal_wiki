const API = "/api";

let currentFile = null;
let autosaveEnabled = true;
let autosaveTimer = null;
let editingEnabled = localStorage.getItem('editingEnabled') === 'true';

/* ---------------- FILE TREE ---------------- */
async function loadFiles() {
    const res = await fetch(`${API}/files`);
    const files = await res.json();

    const tree = buildTree(files);
    renderTree(tree, document.getElementById("file-tree"));
}

function buildTree(paths) {
    const root = {};
    paths.forEach(path => {
        const parts = path.split('/');
        let current = root;
        parts.forEach((part, index) => {
            if (!current[part]) {
                current[part] = (index === parts.length - 1) ? null : {};
            }
            current = current[part];
        });
    });
    return root;
}

function renderTree(tree, container, prefix = "") {
    container.innerHTML = "";
    Object.keys(tree).forEach(key => {
        const li = document.createElement("li");
        if (tree[key] === null) {
            li.textContent = key;
            li.dataset.path = prefix + key;
            li.classList.add('file-item');
            li.onclick = (e) => { e.stopPropagation(); loadPage(prefix + key); };
        } else {
            li.textContent = key;
            li.classList.add("folder");

            const subList = document.createElement("ul");
            subList.style.display = "none";

            li.onclick = (e) => {
                e.stopPropagation();
                subList.style.display = subList.style.display === "none" ? "block" : "none";
            };

            renderTree(tree[key], subList, prefix + key + "/");
            li.appendChild(subList);
        }

        container.appendChild(li);
    });
}

function highlightSelected(filename) {
    const items = document.querySelectorAll('#file-tree li');
    items.forEach(li => {
        if (li.dataset && li.dataset.path) {
            if (li.dataset.path === filename) li.classList.add('selected');
            else li.classList.remove('selected');
        }
    });
}

/* ---------------- PAGE LOAD ---------------- */
async function loadPage(filename) {
    currentFile = filename;

    const res = await fetch(`${API}/page/${filename}`);
    const data = await res.json();

    document.getElementById("editor").value = data.raw;
    document.getElementById("viewer").innerHTML = marked.parse(data.raw);
    const cf = document.getElementById('current-file');
    if (cf) cf.textContent = filename;
    // highlight selected file in tree
    try { highlightSelected(filename); } catch (e) {}
}

/* ---------------- PAGE SAVE ---------------- */
async function savePage(filename) {
    const content = document.getElementById("editor").value;

    await fetch(`${API}/page/${filename}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content })
    });

    document.getElementById("viewer").innerHTML = marked.parse(content);
    // refresh tree
    loadFiles();
}

/* ---------------- NEW / DELETE ---------------- */
const newFileBtn = document.getElementById('new-file');
if (newFileBtn) {
    newFileBtn.onclick = async () => {
        const name = prompt('New file path (relative, e.g. notes/new.md):');
        if (!name) return;
        await savePage(name);
        loadFiles();
        loadPage(name);
    };
}

const deleteFileBtn = document.getElementById('delete-file');
if (deleteFileBtn) {
    deleteFileBtn.onclick = async () => {
        if (!currentFile) return alert('No file selected');
        const ok = confirm('Delete ' + currentFile + '?');
        if (!ok) return;
        const res = await fetch(`${API}/page/${currentFile}`, { method: 'DELETE' });
        if (res.ok) {
            currentFile = null;
            const editorEl = document.getElementById('editor');
            if (editorEl) editorEl.value = '';
            const viewerEl = document.getElementById('viewer');
            if (viewerEl) viewerEl.innerHTML = '';
            const cf = document.getElementById('current-file');
            if (cf) cf.textContent = '(none)';
            loadFiles();
        } else {
            alert('Delete failed');
        }
    };
}

// Logout button in settings
const logoutBtn = document.getElementById('logout-btn');
if (logoutBtn) {
    logoutBtn.onclick = async () => {
        try {
            // call logout and redirect to login page
            await fetch('/logout', { method: 'GET', credentials: 'same-origin' });
        } catch (e) {}
        window.location.href = '/login';
    };
}

// Ctrl+S to save
document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        if (!currentFile) {
            const name = prompt('Save as (relative path):');
            if (!name) return;
            savePage(name).then(() => { loadFiles(); loadPage(name); });
        } else {
            savePage(currentFile);
        }
    }
});

/* ---------------- AUTOSAVE ---------------- */
document.getElementById("editor").addEventListener("input", () => {
    const raw = document.getElementById("editor").value;
    document.getElementById("viewer").innerHTML = marked.parse(raw);

    if (!autosaveEnabled) return;

    clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(() => {
        if (currentFile) savePage(currentFile);
    }, 1200);
});

/* ---------------- SEARCH ---------------- */
document.getElementById("search").addEventListener("input", (e) => {
    const term = e.target.value.toLowerCase();
    const items = document.querySelectorAll("#file-tree li");

    items.forEach(item => {
        item.style.display = item.textContent.toLowerCase().includes(term) ? "block" : "none";
    });
});

/* ---------------- SETTINGS MENU ---------------- */
document.getElementById("settings-btn").onclick = () => {
    document.getElementById("settings-page").style.display = "block";
};

document.getElementById('settings-back').onclick = () => {
    document.getElementById('settings-page').style.display = 'none';
};

// Light mode toggle: when checked -> light theme
const themeToggle = document.getElementById('toggle-dark');
if (themeToggle) {
    themeToggle.checked = document.body.classList.contains('light');
    themeToggle.onclick = () => {
        document.body.classList.toggle('light');
    };
}

// Autosave default true
const autosaveToggle = document.getElementById('toggle-autosave');
if (autosaveToggle) {
    autosaveToggle.checked = true;
    autosaveEnabled = true;
    autosaveToggle.onchange = (e) => { autosaveEnabled = e.target.checked; };
}

document.getElementById("font-size").oninput = (e) => {
    document.getElementById("editor").style.fontSize = e.target.value + "px";
};

/* ---------------- EDIT MODE ---------------- */
function applyEditingMode() {
    const editorCol = document.getElementById('editor-column');
    const viewer = document.getElementById('viewer');
    const toggle = document.getElementById('toggle-editing');
    if (editingEnabled) {
        editorCol.style.display = 'flex';
        if (toggle) toggle.checked = true;
        viewer.style.flex = '1';
    } else {
        editorCol.style.display = 'none';
        if (toggle) toggle.checked = false;
        viewer.style.flex = '1 1 100%';
    }
}

const editingToggle = document.getElementById('toggle-editing');
if (editingToggle) {
    editingToggle.checked = editingEnabled;
    editingToggle.onchange = (e) => {
        editingEnabled = e.target.checked;
        localStorage.setItem('editingEnabled', editingEnabled ? 'true' : 'false');
        applyEditingMode();
        applySplitMode();
    };
}

applyEditingMode();

/* ---------------- SPLIT VIEW / PREVIEW ---------------- */
let splitView = localStorage.getItem('splitView') === 'true';
const splitToggle = document.getElementById('toggle-split');
if (splitToggle) {
    splitToggle.checked = splitView;
    splitToggle.onchange = (e) => {
        splitView = e.target.checked;
        localStorage.setItem('splitView', splitView ? 'true' : 'false');
        applySplitMode();
    };
}

function applySplitMode() {
    const viewer = document.getElementById('viewer');
    const editor = document.getElementById('editor');
    const editorCol = document.getElementById('editor-column');
    const effectiveSplit = splitView && editingEnabled;
    if (effectiveSplit) {
        // side-by-side editor + live preview
        viewer.style.display = 'block';
        editorCol.style.display = 'flex';
        viewer.style.width = '50%';
        editor.style.width = '50%';
        // ensure editor visible
        editorCol.style.flexDirection = 'column';
    } else {
        // no split: either editing-only or preview-only
        if (editingEnabled) {
            // editing mode: show editor column, hide viewer
            editorCol.style.display = 'flex';
            viewer.style.display = 'none';
            // reset widths
            viewer.style.width = '';
            editor.style.width = '';
        } else {
            // preview-only: hide editor, show viewer full-width
            editorCol.style.display = 'none';
            viewer.style.display = 'block';
            viewer.style.width = '';
        }
    }
}

applySplitMode();

/* OpenWebUI URL is embedded via frontend/config.js */

/* ---------------- AI CHAT (embedded OpenWebUI) ---------------- */
document.getElementById("chat-open").onclick = () => {
    const panel = document.getElementById("chat-panel");
    if (!panel) return;
    const isVisible = window.getComputedStyle(panel).display !== 'none';
    // toggle: hide if visible
    if (isVisible) {
        panel.style.display = 'none';
        return;
    }
    const iframe = document.getElementById('openwebui-iframe');
    if (typeof OPENWEBUI_URL !== 'undefined' && iframe) {
        iframe.src = OPENWEBUI_URL;
        const link = document.getElementById('openwebui-link');
        if (link) link.href = OPENWEBUI_URL;
    }
    // apply saved position/size
    applyChatPanelState();
    panel.style.display = "flex";
};

document.getElementById("chat-close").onclick = () => {
    document.getElementById("chat-panel").style.display = "none";
};

/* ---------------- Chat panel drag/resize persistence ---------------- */
function applyChatPanelState() {
    const panel = document.getElementById('chat-panel');
    if (!panel) return;
    const raw = localStorage.getItem('chatPanel');
    if (!raw) return;
    try {
        const s = JSON.parse(raw);
        if (s.left) panel.style.left = s.left;
        if (s.top) panel.style.top = s.top;
        if (s.width) panel.style.width = s.width + 'px';
        if (s.height) panel.style.height = s.height + 'px';
        // clear right/bottom to allow manual positioning
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
    } catch (e) {}
}

// drag support
(() => {
    const panel = document.getElementById('chat-panel');
    const toolbar = document.getElementById('chat-toolbar');
    if (!panel || !toolbar) return;
    let dragging = false, ox = 0, oy = 0;

    toolbar.addEventListener('pointerdown', (e) => {
        // ignore pointerdown when clicking toolbar buttons or links so clicks still register
        if (e.target && e.target.closest && e.target.closest('button, a')) return;
        dragging = true;
        const rect = panel.getBoundingClientRect();
        ox = e.clientX - rect.left;
        oy = e.clientY - rect.top;
        try { toolbar.setPointerCapture && toolbar.setPointerCapture(e.pointerId); } catch (err) {}
    });

    document.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        const left = Math.max(8, e.clientX - ox);
        const top = Math.max(8, e.clientY - oy);
        panel.style.left = left + 'px';
        panel.style.top = top + 'px';
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
    });

    document.addEventListener('pointerup', (e) => {
        if (!dragging) return;
        dragging = false;
        // save
        const rect = panel.getBoundingClientRect();
        const state = { left: rect.left + 'px', top: rect.top + 'px', width: rect.width, height: rect.height };
        localStorage.setItem('chatPanel', JSON.stringify(state));
    });

    // save size on mouseup after resize
    window.addEventListener('mouseup', () => {
        const rect = panel.getBoundingClientRect();
        const state = { left: panel.style.left || rect.left + 'px', top: panel.style.top || rect.top + 'px', width: rect.width, height: rect.height };
        localStorage.setItem('chatPanel', JSON.stringify(state));
    });
})();

/* ---------------- INIT ---------------- */
loadFiles();

// Sidebar toggle: apply saved state and handler
(() => {
    const toggle = document.getElementById('sidebar-toggle');
    const collapsed = localStorage.getItem('sidebarCollapsed') === 'true';
    if (collapsed) document.body.classList.add('sidebar-collapsed');
    if (!toggle) return;
    toggle.onclick = () => {
        const isCollapsed = document.body.classList.toggle('sidebar-collapsed');
        localStorage.setItem('sidebarCollapsed', isCollapsed ? 'true' : 'false');
    };
})();

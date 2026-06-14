// Global State
let sessionId = localStorage.getItem('sessionId') || null;
let currentUser = JSON.parse(localStorage.getItem('currentUser')) || null;
let currentRoom = localStorage.getItem('currentRoom') || null;
let currentRoomData = null;
let currentPmUser = null;
let isRegistering = false;
let typingTimeout = null;
let roomMembersInterval = null;

// --- Utilities ---
function escapeHTML(str) {
    if (!str) return "";
    return str.toString()
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function formatBytes(bytes, decimals = 2) {
    if (!+bytes) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

// --- Upload Queue Manager ---
class UploadQueue {
    constructor(maxConcurrent = 2) {
        this.queue = [];
        this.activeCount = 0;
        this.maxConcurrent = maxConcurrent;
        this.transfers = new Map(); // id -> { file, progress, status, xhr, controller }
        this.container = null;
    }

    initUI() {
        this.container = document.getElementById('upload-queue-container');
    }

    add(file, roomName) {
        const id = Math.random().toString(36).substr(2, 9);
        const item = { id, file, roomName, progress: 0, status: 'queued', xhr: null };
        this.queue.push(item);
        this.transfers.set(id, item);
        this.render();
        this.process();
    }

    async process() {
        if (this.activeCount >= this.maxConcurrent || this.queue.length === 0) return;

        const nextIdx = this.queue.findIndex(i => i.status === 'queued');
        if (nextIdx === -1) return;

        const item = this.queue.splice(nextIdx, 1)[0];
        item.status = 'uploading';
        this.activeCount++;
        this.render();

        try {
            await this.uploadFile(item);
            item.status = 'completed';
            item.progress = 100;
        } catch (err) {
            if (item.status === 'cancelled') {
            } else if (item.status === 'paused') {
                this.queue.unshift(item);
            } else {
                item.status = 'failed';
                showToast(`Upload failed for ${item.file.name}: ${err.message}`, 'error');
            }
        } finally {
            this.activeCount--;
            this.render();
            
            if (item.status === 'completed' || item.status === 'failed' || item.status === 'cancelled') {
                setTimeout(() => {
                    this.transfers.delete(item.id);
                    this.render();
                }, 5000);
            }
            this.process();
        }
    }

    pause(id) {
        const item = this.transfers.get(id);
        if (!item) return;
        if (item.status === 'uploading') {
            item.status = 'paused';
            if (item.activeXHRs) {
                for (const xhr of item.activeXHRs) {
                    xhr.abort();
                }
            }
        } else if (item.status === 'queued') {
            item.status = 'paused';
        }
        this.render();
    }

    resume(id) {
        const item = this.transfers.get(id);
        if (!item || item.status !== 'paused') return;
        item.status = 'queued';
        this.queue.push(item);
        this.render();
        this.process();
    }

    cancel(id) {
        const item = this.transfers.get(id);
        if (!item) return;
        const oldStatus = item.status;
        item.status = 'cancelled';
        if (oldStatus === 'uploading') {
            if (item.activeXHRs) {
                for (const xhr of item.activeXHRs) {
                    xhr.abort();
                }
            }
        } else {
            this.queue = this.queue.filter(i => i.id !== id);
        }
        this.render();
        setTimeout(() => {
            this.transfers.delete(id);
            this.render();
        }, 2000);
    }

    async calculateFileSHA256(file) {
        const arrayBuffer = await file.arrayBuffer();
        const hashBuffer = await crypto.subtle.digest('SHA-256', arrayBuffer);
        const hashArray = Array.from(new Uint8Array(hashBuffer));
        const hashHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
        return hashHex;
    }

    async uploadFile(item) {
        let startChunk = 0;
        let transferId = item.transfer_id || null;

        if (transferId) {
            try {
                const res = await apiCall(`/rooms/files/resume?transfer_id=${transferId}&direction=upload`);
                startChunk = res.start_chunk || 0;
            } catch (e) {
                transferId = null;
                item.transfer_id = null;
            }
        }

        // Calculate dynamic chunk size to prevent port exhaustion (1MB to 16MB)
        let chunkSize = 1024 * 1024;
        if (item.file.size > 100 * 1024 * 1024) {
            const mb = Math.ceil(item.file.size / (100 * 1024 * 1024));
            chunkSize = Math.min(16, mb) * 1024 * 1024;
        }
        const totalChunks = Math.ceil(item.file.size / chunkSize);


        if (!transferId) {
            const checksum = await this.calculateFileSHA256(item.file);
            const initRes = await apiCall(`/rooms/files/upload?action=init&room_name=${encodeURIComponent(item.roomName)}&filename=${encodeURIComponent(item.file.name)}&filesize=${item.file.size}&checksum_sha256=${checksum}`, 'POST');
            transferId = initRes.transfer_id;
            item.transfer_id = transferId;
        }

        item.startTime = Date.now();
        item.startBytes = startChunk * chunkSize;
        item.speed = '';

        const maxConcurrentChunks = 4;
        const activeXHRs = new Set();
        item.activeXHRs = activeXHRs;
        let currentChunkIndex = startChunk;
        let hasError = null;

        const uploadWorker = async () => {
            while (currentChunkIndex < totalChunks && !hasError) {
                if (item.status === 'paused' || item.status === 'cancelled') {
                    throw new Error(item.status);
                }
                const i = currentChunkIndex++;
                if (i >= totalChunks) break;
                const chunkStart = i * chunkSize;
                const chunkEnd = Math.min(chunkStart + chunkSize, item.file.size);
                const chunkBlob = item.file.slice(chunkStart, chunkEnd);

                await new Promise((resolve, reject) => {
                    if (item.status === 'paused' || item.status === 'cancelled') {
                        reject(new Error(item.status));
                        return;
                    }
                    const xhr = new XMLHttpRequest();
                    activeXHRs.add(xhr);
                    const url = `/api/rooms/files/upload?action=chunk&transfer_id=${transferId}&chunk_index=${i}&room_name=${encodeURIComponent(item.roomName)}&filename=${encodeURIComponent(item.file.name)}`;
                    xhr.open('POST', url, true);
                    xhr.setRequestHeader('Session-Id', sessionId);
                    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
                    xhr.upload.onprogress = (e) => {
                        if (e.lengthComputable) {
                            if (!item.chunkProgresses) item.chunkProgresses = {};
                            item.chunkProgresses[i] = e.loaded;
                            let bytesAlreadyUploaded = startChunk * chunkSize;
                            for (const idx in item.chunkProgresses) {
                                bytesAlreadyUploaded += item.chunkProgresses[idx];
                            }
                            if (bytesAlreadyUploaded > item.file.size) bytesAlreadyUploaded = item.file.size;
                            item.progress = Math.round((bytesAlreadyUploaded / item.file.size) * 100);
                            const now = Date.now();
                            const elapsed = (now - item.startTime) / 1000;
                            if (elapsed > 0) {
                                const bytesSentInSession = bytesAlreadyUploaded - item.startBytes;
                                item.speed = `${formatBytes(bytesSentInSession / elapsed)}/s`;
                            }
                            this.render();
                        }
                    };
                    xhr.onload = () => {
                        activeXHRs.delete(xhr);
                        if (xhr.status >= 200 && xhr.status < 300) resolve();
                        else { hasError = new Error(xhr.statusText); reject(hasError); }
                    };
                    xhr.onerror = () => { activeXHRs.delete(xhr); hasError = new Error('Network error'); reject(hasError); };
                    xhr.onabort = () => { activeXHRs.delete(xhr); reject(new Error('Aborted')); };
                    xhr.send(chunkBlob);
                });
            }
        };

        const workers = [];
        for (let w = 0; w < Math.min(maxConcurrentChunks, totalChunks - startChunk); w++) {
            workers.push(uploadWorker());
        }

        try {
            await Promise.all(workers);
        } catch (err) {
            for (const xhr of activeXHRs) xhr.abort();
            throw err;
        } finally {
            item.activeXHRs = null;
        }

        const finishRes = await apiCall(`/rooms/files/upload?action=finish&transfer_id=${transferId}&room_name=${encodeURIComponent(item.roomName)}&filename=${encodeURIComponent(item.file.name)}`, 'POST');
        item.speed = '';
        return finishRes;
    }

    render() {
        if (!this.container) return;
        if (this.transfers.size === 0) {
            this.container.classList.add('hidden');
            return;
        }
        this.container.classList.remove('hidden');
        this.container.innerHTML = `<h5 class="text-[10px] uppercase font-bold text-gray-500 mb-2 px-1 flex justify-between">File Transfers <span class="text-primary cursor-pointer hover:underline" onclick="uploadQueue.clearFinished()">Clear</span></h5>`;
        this.transfers.forEach(item => {
            const div = document.createElement('div');
            div.className = 'bg-black/40 p-2 rounded mb-1 text-xs border border-gray-800/50 group relative';
            const statusColors = { queued: 'text-gray-500', paused: 'text-yellow-500', uploading: 'text-primary', completed: 'text-green-500', failed: 'text-red-500', cancelled: 'text-gray-600' };
            let controls = '';
            if (item.status === 'uploading' || item.status === 'queued') {
                controls = `<button onclick="uploadQueue.pause('${item.id}')" class="hover:text-yellow-500"><svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zM7 8v4a1 1 0 002 0V8a1 1 0 00-2 0zm4 0v4a1 1 0 002 0V8a1 1 0 00-2 0z"/></svg></button>`;
            } else if (item.status === 'paused') {
                controls = `<button onclick="uploadQueue.resume('${item.id}')" class="hover:text-green-500"><svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"/></svg></button>`;
            }
            if (['uploading', 'queued', 'paused'].includes(item.status)) {
                controls += `<button onclick="uploadQueue.cancel('${item.id}')" class="ml-1 hover:text-red-500"><svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"/></svg></button>`;
            }
            const speedText = (item.status === 'uploading' && item.speed) ? ` • ${item.speed}` : '';
            div.innerHTML = `
                <div class="flex justify-between mb-1 truncate">
                    <span class="truncate pr-8 font-medium">${escapeHTML(item.file.name)}</span>
                    <div class="flex items-center gap-1">
                        <span class="${statusColors[item.status]} uppercase font-bold text-[9px]">${item.status}</span>
                        <div class="flex items-center ml-1 text-gray-400">${controls}</div>
                    </div>
                </div>
                <div class="w-full bg-gray-800 rounded-full h-1.5 overflow-hidden">
                    <div class="h-full bg-primary transition-all duration-300 ${item.status === 'paused' ? 'opacity-50' : ''}" style="width: ${item.progress}%"></div>
                </div>
                <div class="flex justify-between mt-1 text-[10px] text-gray-500">
                    <span>${formatBytes(item.file.size)}${speedText}</span>
                    <span>${item.progress}%</span>
                </div>
            `;
            this.container.appendChild(div);
        });
    }

    clearFinished() {
        this.transfers.forEach((item, id) => { if (['completed', 'failed', 'cancelled'].includes(item.status)) this.transfers.delete(id); });
        this.render();
    }
}

const uploadQueue = new UploadQueue();

// Global Error Handler
window.onerror = function(msg, url, line, col, error) {
    showToast(`JS Error: ${escapeHTML(msg)}`, 'error');
    console.error(error);
};

// --- Admin Actions ---
window.kickUser = async function(username) {
    if (!confirm(`Are you sure you want to kick ${username}?`)) return;
    try {
        await apiCall('/rooms/kick', 'POST', { username });
        showToast(`User ${escapeHTML(username)} kicked.`, 'success');
    } catch (e) {}
}

window.refreshRoomMessages = async function() {
    if (!currentRoom) return;
    try {
        const data = await apiCall(`/rooms/messages?room_name=${currentRoom}`);
        UI.roomChatHistory.innerHTML = '';
        data.messages.forEach(msg => {
            appendMessage(UI.roomChatHistory, msg.sender_username, msg.message, msg.timestamp, msg.message_type, msg.message_id, msg.reactions);
        });
        UI.roomChatHistory.scrollTop = UI.roomChatHistory.scrollHeight;
    } catch(e) {}
}

window.deleteFile = async function(fileId) {
    if (!confirm(`Are you sure you want to delete this file?`)) return;
    try {
        await apiCall('/rooms/files/delete', 'POST', { file_id: fileId });
        showToast(`File deleted.`, 'success');
        await window.refreshRoomMessages();
    } catch (e) {}
}

// DOM Elements
const views = { auth: document.getElementById('view-auth'), dashboard: document.getElementById('view-dashboard'), room: document.getElementById('view-room') };
const UI = {
    navUserInfo: document.getElementById('nav-user-info'),
    navUsername: document.getElementById('nav-username'),
    listOnlineUsers: document.getElementById('list-online-users'),
    listRooms: document.getElementById('list-rooms'),
    pmView: document.getElementById('pm-view'),
    pmHistory: document.getElementById('pm-history'),
    pmTitle: document.getElementById('pm-title'),
    roomChatHistory: document.getElementById('room-chat-history'),
    roomTitle: document.getElementById('room-title'),
    roomMembersList: document.getElementById('room-members-list'),
    roomMemberCount: document.getElementById('room-member-count'),
    toastContainer: document.getElementById('toast-container'),
    modalCreateRoom: document.getElementById('modal-create-room'),
    modalRoomFiles: document.getElementById('modal-room-files'),
    listRoomFiles: document.getElementById('list-room-files'),
};

function showView(viewName) {
    Object.values(views).forEach(v => v.classList.add('hidden'));
    views[viewName].classList.remove('hidden');
    if (viewName !== 'auth') UI.navUserInfo.classList.remove('hidden');
    else UI.navUserInfo.classList.add('hidden');
}

window.addEventListener('DOMContentLoaded', () => {
    uploadQueue.initUI();
    if (sessionId && currentUser) {
        UI.navUsername.textContent = currentUser.display_name;
        if (currentRoom) joinRoom(currentRoom);
        else { showView('dashboard'); refreshDashboard(); }
        startPolling();
    }
});

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    const color = type === 'error' ? 'bg-red-500' : (type === 'success' ? 'bg-green-500' : 'bg-primary');
    toast.className = `${color} text-white px-4 py-2 rounded shadow-lg text-sm mb-2 opacity-0 transition-opacity duration-300`;
    toast.textContent = message;
    UI.toastContainer.appendChild(toast);
    setTimeout(() => toast.classList.remove('opacity-0'), 10);
    setTimeout(() => { toast.classList.add('opacity-0'); setTimeout(() => toast.remove(), 300); }, 3000);
}

async function apiCall(path, method = 'GET', body = null) {
    const headers = {};
    if (!(body instanceof FormData)) headers['Content-Type'] = 'application/json';
    if (sessionId) headers['Session-Id'] = sessionId;
    const options = { method, headers };
    if (body) options.body = body instanceof FormData ? body : JSON.stringify(body);
    try {
        const res = await fetch(`/api${path}`, options);
        if (res.status === 401) { document.getElementById('btn-logout').click(); throw new Error('Session expired'); }
        if (res.headers.get('content-type')?.includes('application/json')) {
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'API Error');
            return data;
        } else { if (!res.ok) throw new Error('API Error'); return res.blob(); }
    } catch (err) { if (err.message !== 'Session expired') showToast(err.message, 'error'); throw err; }
}

document.getElementById('link-toggle-auth').addEventListener('click', (e) => {
    e.preventDefault();
    isRegistering = !isRegistering;
    document.getElementById('auth-title').textContent = isRegistering ? 'Register' : 'Login';
    document.getElementById('btn-auth-submit').textContent = isRegistering ? 'Register' : 'Login';
    document.getElementById('auth-display-name-group').classList.toggle('hidden', !isRegistering);
    e.target.textContent = isRegistering ? 'Already have an account? Login' : 'Need an account? Register';
});

document.getElementById('auth-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('auth-username').value;
    const password = document.getElementById('auth-password').value;
    const display_name = document.getElementById('auth-display-name').value || username;
    if (isRegistering) {
        try { await apiCall('/register', 'POST', { username, password, display_name }); showToast('Registration successful! Please login.', 'success'); document.getElementById('link-toggle-auth').click(); } catch (e) {}
    } else {
        try {
            const data = await apiCall('/login', 'POST', { username, password });
            sessionId = data.session_id; currentUser = data.user;
            localStorage.setItem('sessionId', sessionId); localStorage.setItem('currentUser', JSON.stringify(currentUser));
            UI.navUsername.textContent = currentUser.display_name; showView('dashboard'); startPolling(); refreshDashboard();
        } catch (e) {}
    }
});

document.getElementById('btn-logout').addEventListener('click', () => {
    sessionId = null; currentUser = null; currentRoom = null;
    localStorage.removeItem('sessionId'); localStorage.removeItem('currentUser'); localStorage.removeItem('currentRoom');
    showView('auth');
});

async function refreshDashboard() {
    if (!views.dashboard.classList.contains('hidden')) {
        try {
            const [usersData, roomsData] = await Promise.all([apiCall('/users'), apiCall('/rooms')]);
            UI.listOnlineUsers.innerHTML = '';
            usersData.users.forEach(u => {
                if (u.username === currentUser.username) return;
                const div = document.createElement('div');
                div.className = 'flex items-center justify-between p-2 hover:bg-gray-800 rounded cursor-pointer transition-colors group';
                div.innerHTML = `<div class="flex items-center"><div class="w-2 h-2 rounded-full bg-green-500 mr-2"></div><span class="text-sm">${escapeHTML(u.username)}</span></div><button class="opacity-0 group-hover:opacity-100 text-xs bg-primary hover:bg-secondary px-2 py-1 rounded transition-all">Chat</button>`;
                div.addEventListener('click', () => openPM(u.username));
                UI.listOnlineUsers.appendChild(div);
            });
            UI.listRooms.innerHTML = '';
            roomsData.rooms.forEach(r => {
                const div = document.createElement('div');
                div.className = 'bg-dark p-4 rounded border border-gray-800 hover:border-gray-600 transition-colors flex flex-col justify-between';
                div.innerHTML = `<div><h4 class="font-semibold text-primary mb-1">${escapeHTML(r.name)}</h4><p class="text-xs text-gray-500 mb-2">${escapeHTML(r.description || 'No description')}</p></div><div class="flex justify-between items-end mt-2"><span class="text-xs text-gray-600">${r.members || 0} active</span><button class="bg-gray-800 hover:bg-gray-700 text-xs px-3 py-1 rounded transition-colors" onclick="joinRoom('${r.name.replace(/'/g, "\\'")}')">Join</button></div>`;
                UI.listRooms.appendChild(div);
            });
        } catch (e) {}
    }
}
setInterval(refreshDashboard, 10000);
document.getElementById('btn-refresh-users').addEventListener('click', refreshDashboard);

async function openPM(username) {
    currentPmUser = username; UI.pmTitle.textContent = `Chat with ${username}`; UI.pmView.classList.remove('hidden');
    UI.pmHistory.innerHTML = '<div class="text-center text-gray-500 text-sm mt-4">Loading history...</div>';
    try {
        const data = await apiCall(`/pm/history?other_username=${username}`);
        UI.pmHistory.innerHTML = '';
        data.messages.forEach(msg => appendMessage(UI.pmHistory, msg.sender_username, msg.content, msg.timestamp));
        UI.pmHistory.scrollTop = UI.pmHistory.scrollHeight;
    } catch(e) {}
}
document.getElementById('btn-close-pm').addEventListener('click', () => { UI.pmView.classList.add('hidden'); currentPmUser = null; });
document.getElementById('pm-form').addEventListener('submit', async (e) => {
    e.preventDefault(); const input = document.getElementById('pm-input'); const content = input.value.trim();
    if (!content || !currentPmUser) return;
    input.value = ''; appendMessage(UI.pmHistory, currentUser.username, content, new Date().toLocaleTimeString());
    try { await apiCall('/pm', 'POST', { recipient_username: currentPmUser, content }); } catch(e) {}
});

async function fetchRoomMembers() {
    if (!currentRoom || views.room.classList.contains('hidden')) return;
    try {
        const data = await apiCall(`/rooms/members?room_name=${encodeURIComponent(currentRoom)}`);
        UI.roomMembersList.innerHTML = ''; UI.roomMemberCount.textContent = data.members.length;
        data.members.forEach(username => {
            const isMe = username === currentUser.username;
            const div = document.createElement('div');
            div.className = 'flex items-center p-2 rounded hover:bg-gray-800/50 transition-colors';
            div.innerHTML = `<div class="w-2 h-2 rounded-full ${isMe ? 'bg-blue-500' : 'bg-green-500'} mr-2"></div><span class="text-sm ${isMe ? 'font-bold text-white' : 'text-gray-300'}">${escapeHTML(username)}</span>${isMe ? '<span class="ml-2 text-[9px] bg-gray-700 px-1 rounded text-gray-400">ME</span>' : ''}`;
            UI.roomMembersList.appendChild(div);
        });
    } catch (e) {}
}

function sendTypingIndicator(isTyping) { if (currentRoom) apiCall('/rooms/typing', 'POST', { is_typing: isTyping }).catch(() => {}); }
document.getElementById('room-chat-input').addEventListener('input', () => {
    if (!typingTimeout) sendTypingIndicator(true);
    clearTimeout(typingTimeout);
    typingTimeout = setTimeout(() => { sendTypingIndicator(false); typingTimeout = null; }, 3000);
});

document.getElementById('btn-create-room').addEventListener('click', () => { UI.modalCreateRoom.classList.remove('hidden'); });
document.getElementById('btn-cancel-create-room').addEventListener('click', () => { UI.modalCreateRoom.classList.add('hidden'); });
document.getElementById('form-create-room').addEventListener('submit', async (e) => {
    e.preventDefault(); const room_name = document.getElementById('new-room-name').value.trim(); const description = document.getElementById('new-room-desc').value.trim();
    if(!room_name) return;
    try { await apiCall('/rooms', 'POST', { room_name, description }); UI.modalCreateRoom.classList.add('hidden'); showToast('Room created!', 'success'); refreshDashboard(); } catch(e) {}
});

window.joinRoom = async function(roomName) {
    try {
        await apiCall('/rooms/join', 'POST', { room_name: roomName });
        currentRoom = roomName; localStorage.setItem('currentRoom', roomName); UI.roomTitle.textContent = roomName;
        const roomsData = await apiCall('/rooms'); currentRoomData = roomsData.rooms.find(r => r.name === roomName);
        showView('room'); fetchRoomMembers(); if (roomMembersInterval) clearInterval(roomMembersInterval);
        roomMembersInterval = setInterval(fetchRoomMembers, 5000);
        UI.roomChatHistory.innerHTML = '<div class="text-center text-gray-500 text-sm mt-4">Loading history...</div>';
        const data = await apiCall(`/rooms/messages?room_name=${roomName}`);
        UI.roomChatHistory.innerHTML = '';
        data.messages.forEach(msg => appendMessage(UI.roomChatHistory, msg.sender_username, msg.message, msg.timestamp, msg.message_type, msg.message_id, msg.reactions));
        UI.roomChatHistory.scrollTop = UI.roomChatHistory.scrollHeight;
    } catch(e) {}
}

document.getElementById('btn-leave-room').addEventListener('click', async () => {
    try { await apiCall('/rooms/leave', 'POST'); currentRoom = null; if (roomMembersInterval) clearInterval(roomMembersInterval); localStorage.removeItem('currentRoom'); showView('dashboard'); refreshDashboard(); } catch(e) {}
});

document.getElementById('room-chat-form').addEventListener('submit', async (e) => {
    e.preventDefault(); const input = document.getElementById('room-chat-input'); const message = input.value.trim();
    if (!message || !currentRoom) return;
    input.value = ''; try { await apiCall('/rooms/messages', 'POST', { room_name: currentRoom, message }); } catch(e) {}
});

document.getElementById('btn-chat-attach').addEventListener('click', () => { document.getElementById('chat-file-input').click(); });
document.getElementById('chat-file-input').addEventListener('change', async (e) => {
    if (currentRoom && e.target.files.length > 0) { Array.from(e.target.files).forEach(file => uploadQueue.add(file, currentRoom)); e.target.value = ''; }
});

document.getElementById('btn-room-files').addEventListener('click', async () => {
    if (!currentRoom) return; UI.modalRoomFiles.classList.remove('hidden'); UI.listRoomFiles.innerHTML = '<div class="text-center text-sm text-gray-500 py-4">Loading files...</div>';
    try {
        const data = await apiCall(`/rooms/files?room_name=${encodeURIComponent(currentRoom)}`);
        UI.listRoomFiles.innerHTML = ''; if (data.files.length === 0) UI.listRoomFiles.innerHTML = '<div class="text-center text-sm text-gray-500 py-4">No files in this room.</div>';
        data.files.forEach(f => {
            const div = document.createElement('div'); div.className = 'flex justify-between items-center p-2 hover:bg-gray-800 rounded transition-colors';
            div.innerHTML = `<div class="flex flex-col pr-2 min-w-0"><span class="text-sm font-medium truncate">${escapeHTML(f.original_filename)}</span><span class="text-xs text-gray-500">${formatBytes(f.size_bytes)} • by ${escapeHTML(f.uploader_username)}</span></div><button class="bg-secondary hover:bg-primary text-xs px-3 py-1 rounded transition-colors flex-shrink-0" onclick="downloadFile(${f.file_id}, '${f.original_filename.replace(/'/g, "\\'")}')">Download</button>`;
            UI.listRoomFiles.appendChild(div);
        });
    } catch(e) {}
});
document.getElementById('btn-close-files').addEventListener('click', () => { UI.modalRoomFiles.classList.add('hidden'); });
document.getElementById('form-upload-file').addEventListener('submit', (e) => {
    e.preventDefault(); const fileInput = document.getElementById('file-input');
    if (currentRoom && fileInput.files.length > 0) { Array.from(fileInput.files).forEach(file => uploadQueue.add(file, currentRoom)); fileInput.value = ''; showToast('Files added to queue!', 'success'); }
});

window.downloadFile = async function(fileId, filename) {
    showToast(`Starting download for ${escapeHTML(filename)}...`);
    try {
        const blob = await apiCall(`/rooms/files/download?file_id=${fileId}`);
        const url = window.URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove(); window.URL.revokeObjectURL(url);
    } catch (e) {}
}

const COMMON_EMOJIS = ["👍", "❤️", "😂", "😮", "😢", "🔥", "🎉", "👏", "🤔", "👀", "✨", "🚀"];
const emojiMenu = document.getElementById('emoji-menu');
COMMON_EMOJIS.forEach(emoji => {
    const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'hover:bg-gray-800 p-2 rounded transition-colors text-lg'; btn.textContent = emoji;
    btn.onclick = () => { const input = document.getElementById('room-chat-input'); input.value += emoji; input.focus(); emojiMenu.classList.add('hidden'); }; emojiMenu.appendChild(btn);
});
document.getElementById('btn-emoji-picker').addEventListener('click', (e) => { e.stopPropagation(); emojiMenu.classList.toggle('hidden'); });
document.addEventListener('click', () => { emojiMenu.classList.add('hidden'); });

async function addReaction(messageId, emoji, action = 'add') { if (messageId) try { await apiCall('/rooms/reactions', 'POST', { message_id: messageId, emoji: emoji, action: action }); } catch (e) {} }
window.addReaction = addReaction;
function toggleReaction(messageId, emoji) {
    const msgDiv = document.querySelector(`[data-message-id="${messageId}"]`); if (!msgDiv) return addReaction(messageId, emoji, 'add');
    const reactionSpan = Array.from(msgDiv.querySelectorAll('.reaction-container span')).find(s => s.innerHTML.includes(emoji));
    let action = (reactionSpan && reactionSpan.title.includes(currentUser.username)) ? 'remove' : 'add'; addReaction(messageId, emoji, action);
}
window.toggleReaction = toggleReaction;

function updateMessageReactions(messageId, reactions) {
    const msgDiv = document.querySelector(`[data-message-id="${messageId}"]`); if (!msgDiv) return;
    let reactionContainer = msgDiv.querySelector('.reaction-container');
    if (!reactionContainer) { reactionContainer = document.createElement('div'); reactionContainer.className = 'reaction-container flex flex-wrap gap-1 mt-1'; msgDiv.querySelector('.message-bubble-wrapper').appendChild(reactionContainer); }
    reactionContainer.innerHTML = ''; if (!reactions || Object.keys(reactions).length === 0) { reactionContainer.classList.add('hidden'); return; }
    reactionContainer.classList.remove('hidden');
    for (const [emoji, data] of Object.entries(reactions)) {
        const count = typeof data === 'object' ? data.count : data; const usernames = typeof data === 'object' ? data.usernames : []; const hasReacted = usernames.includes(currentUser.username);
        const span = document.createElement('span'); span.className = `inline-flex items-center ${hasReacted ? 'bg-primary/30 border-primary/50' : 'bg-black/30 border-transparent'} hover:bg-black/50 px-1.5 py-0.5 rounded text-xs cursor-pointer transition-colors border hover:border-gray-600`;
        span.title = usernames.join(', '); span.innerHTML = `<span>${emoji}</span> <span class="ml-1 font-bold text-gray-300">${count}</span>`;
        span.onclick = (e) => { e.stopPropagation(); toggleReaction(messageId, emoji); }; reactionContainer.appendChild(span);
    }
}

function appendMessage(container, sender, text, time, type = 'text', messageId = null, initialReactions = null) {
    if (type === 'system') { appendSystemMessage(container, text); return; }
    const isMe = sender === currentUser.username; const div = document.createElement('div'); div.className = `flex flex-col ${isMe ? 'items-end' : 'items-start'} mb-4 group relative`;
    if (messageId) div.setAttribute('data-message-id', messageId);
    let contentHtml = ""; let fileData = null;
    if (type === 'file') {
        try {
            fileData = typeof text === 'string' ? JSON.parse(text) : text;
            contentHtml = `<div class="flex items-center gap-3 p-3 bg-black/20 rounded-lg cursor-pointer hover:bg-black/30 transition-colors border border-gray-700/50" onclick="downloadFile(${fileData.file_id}, '${fileData.filename.replace(/'/g, "\\'")}')"><div class="bg-primary/20 p-2 rounded-lg"><svg class="w-6 h-6 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg></div><div class="flex flex-col text-sm truncate"><span class="font-medium truncate text-gray-200">${escapeHTML(fileData.filename)}</span><span class="text-xs text-gray-500">${formatBytes(fileData.size_bytes)}</span></div></div>`;
        } catch(e) { contentHtml = '<span class="text-red-400 italic">Invalid file data</span>'; }
    } else { contentHtml = escapeHTML(text); }
    const isOwner = currentRoomData && currentRoomData.owner_id === currentUser.user_id;
    const adminActionsHtml = isOwner ? `<div class="admin-actions flex gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity">${type === 'file' && fileData ? `<button class="text-[9px] bg-red-900/50 hover:bg-red-800 px-1.5 py-0.5 rounded text-red-200 transition-colors" onclick="window.deleteFile(${fileData.file_id})">Delete File</button>` : ''}${!isMe ? `<button class="text-[9px] bg-red-900/50 hover:bg-red-800 px-1.5 py-0.5 rounded text-red-200 transition-colors" onclick="window.kickUser('${sender.replace(/'/g, "\\'")}')">Kick</button>` : ''}</div>` : '';
    const reactionBtnHtml = messageId ? `<div class="reaction-menu absolute ${isMe ? 'right-[calc(100%-5px)]' : 'left-[calc(100%-5px)]'} top-0 opacity-0 pointer-events-none transition-all duration-200 flex gap-1 bg-gray-900 border border-gray-700 rounded-full p-1.5 shadow-2xl z-20 hover:scale-105">${COMMON_EMOJIS.slice(0, 6).map(e => `<button class="hover:scale-125 transition-transform px-1.5 py-0.5 rounded-full hover:bg-gray-800" onclick="window.toggleReaction(${messageId}, '${e}')">${e}</button>`).join('')}</div>` : '';
    div.innerHTML = `<span class="text-[10px] uppercase tracking-wider text-gray-600 mb-1 px-1 font-bold">${escapeHTML(sender)} • ${time}</span><div class="message-bubble-wrapper relative flex flex-col ${isMe ? 'items-end' : 'items-start'} max-w-[85%]">${reactionBtnHtml}<div class="px-4 py-2 rounded-2xl shadow-sm ${isMe ? 'bg-primary text-white rounded-tr-none' : 'bg-gray-800 text-gray-200 rounded-tl-none'} break-words w-fit max-w-full">${contentHtml}</div>${adminActionsHtml}<div class="reaction-container flex flex-wrap gap-1 mt-1 empty:hidden"></div></div>`;
    container.appendChild(div); if (messageId && initialReactions) updateMessageReactions(messageId, initialReactions); container.scrollTop = container.scrollHeight;
}

function appendSystemMessage(container, text) {
    const div = document.createElement('div'); div.className = `flex justify-center mb-2`;
    div.innerHTML = `<div class="px-3 py-1 rounded-full bg-gray-800/50 text-gray-500 text-xs">${escapeHTML(text)}</div>`;
    container.appendChild(div); container.scrollTop = container.scrollHeight;
}

let typingUsers = new Set();
function handleTypingIndicator(payload) {
    if (payload.room_name !== currentRoom) return;
    if (payload.is_typing) typingUsers.add(payload.username); else typingUsers.delete(payload.username);
    const indicatorDiv = document.getElementById('typing-indicator');
    if (typingUsers.size === 0) indicatorDiv.textContent = '';
    else { const users = Array.from(typingUsers).join(', '); indicatorDiv.textContent = `${users} ${typingUsers.size === 1 ? 'is' : 'are'} typing...`; }
}

async function startPolling() {
    if (!sessionId) return;
    try {
        const res = await fetch(`/api/events?session_id=${sessionId}`);
        if (res.ok) { const data = await res.json(); if (data.events) data.events.forEach(handleEvent); }
    } catch (e) { console.error("Polling error", e); await new Promise(r => setTimeout(r, 2000)); }
    startPolling();
}

function handleEvent(ev) {
    if (ev.type === "PM_RECEIVED") {
        const { sender_username, content, timestamp } = ev.payload;
        if (currentPmUser === sender_username && !UI.pmView.classList.contains('hidden')) appendMessage(UI.pmHistory, sender_username, content, timestamp);
        else showToast(`New PM from ${sender_username}: ${content}`);
    } else if (ev.type === "ROOM_MESSAGE") {
        const { sender_username, message, timestamp, message_type } = ev.payload;
        if (currentRoom) appendMessage(UI.roomChatHistory, sender_username, message, timestamp, message_type || 'text', ev.payload.message_id, ev.payload.reactions);
    } else if (ev.type === "ROOM_DELETE_FILE_BROADCAST") {
        const { message_id } = ev.payload; const msgEl = document.querySelector(`[data-message-id="${message_id}"]`); if (msgEl) msgEl.remove();
    } else if (ev.type === "ROOM_REACTION_BROADCAST") updateMessageReactions(ev.payload.message_id, ev.payload.reactions);
    else if (ev.type === "ROOM_TYPING_BROADCAST") handleTypingIndicator(ev.payload);
    else if (ev.type === "SYSTEM_EVENT") { if (currentRoom) { appendSystemMessage(UI.roomChatHistory, ev.payload.message); fetchRoomMembers(); } }
    else if (ev.type === "DISCONNECTED") { showToast(`Disconnected from ${ev.server}`, 'error'); if (ev.server === 'gateway') document.getElementById('btn-logout').click(); }
    else if (ev.type === "ERROR") showToast(ev.message, 'error');
}

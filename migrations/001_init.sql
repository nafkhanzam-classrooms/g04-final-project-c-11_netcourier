CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL,
    last_login_at DATETIME
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    client_ip VARCHAR(50),
    connected_at DATETIME NOT NULL,
    last_seen_at DATETIME NOT NULL,
    disconnected_at DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS backend_servers (
    server_id VARCHAR(20) PRIMARY KEY,
    host VARCHAR(100) NOT NULL,
    port INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'alive',
    active_rooms INTEGER NOT NULL DEFAULT 0,
    active_clients INTEGER NOT NULL DEFAULT 0,
    active_transfers INTEGER NOT NULL DEFAULT 0,
    last_heartbeat_at DATETIME
);

CREATE TABLE IF NOT EXISTS user_presence (
    presence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    username VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'offline',
    server_id VARCHAR(20),
    active_room VARCHAR(100),
    last_seen_at DATETIME NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (server_id) REFERENCES backend_servers(server_id)
);

CREATE TABLE IF NOT EXISTS rooms (
    room_id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_name VARCHAR(100) NOT NULL UNIQUE,
    created_by INTEGER NOT NULL,
    server_id VARCHAR(20) NOT NULL,
    description TEXT,
    visibility VARCHAR(20) NOT NULL DEFAULT 'public',
    created_at DATETIME NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    FOREIGN KEY (created_by) REFERENCES users(user_id),
    FOREIGN KEY (server_id) REFERENCES backend_servers(server_id)
);

CREATE TABLE IF NOT EXISTS room_mapping (
    mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL UNIQUE,
    room_name VARCHAR(100) NOT NULL UNIQUE,
    server_id VARCHAR(20) NOT NULL,
    assigned_at DATETIME NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    FOREIGN KEY (room_id) REFERENCES rooms(room_id),
    FOREIGN KEY (server_id) REFERENCES backend_servers(server_id)
);

CREATE TABLE IF NOT EXISTS room_members (
    member_id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username VARCHAR(50) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'member',
    joined_at DATETIME NOT NULL,
    left_at DATETIME,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    FOREIGN KEY (room_id) REFERENCES rooms(room_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS room_messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    server_id VARCHAR(20) NOT NULL,
    sender_id INTEGER,
    sender_username VARCHAR(50),
    message_type VARCHAR(20) NOT NULL DEFAULT 'text',
    content TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    is_deleted BOOLEAN NOT NULL DEFAULT 0,
    FOREIGN KEY (room_id) REFERENCES rooms(room_id),
    FOREIGN KEY (server_id) REFERENCES backend_servers(server_id),
    FOREIGN KEY (sender_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS private_messages (
    private_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL,
    sender_username VARCHAR(50) NOT NULL,
    recipient_id INTEGER NOT NULL,
    recipient_username VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'sent',
    created_at DATETIME NOT NULL,
    delivered_at DATETIME,
    read_at DATETIME,
    FOREIGN KEY (sender_id) REFERENCES users(user_id),
    FOREIGN KEY (recipient_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    server_id VARCHAR(20) NOT NULL,
    uploader_id INTEGER NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    stored_filename VARCHAR(255) NOT NULL,
    stored_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    chunk_size INTEGER NOT NULL,
    total_chunks INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'uploading',
    uploaded_at DATETIME NOT NULL,
    FOREIGN KEY (room_id) REFERENCES rooms(room_id),
    FOREIGN KEY (server_id) REFERENCES backend_servers(server_id),
    FOREIGN KEY (uploader_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS file_transfers (
    transfer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER,
    room_id INTEGER NOT NULL,
    server_id VARCHAR(20) NOT NULL,
    user_id INTEGER NOT NULL,
    direction VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    total_chunks INTEGER NOT NULL,
    completed_chunks INTEGER NOT NULL DEFAULT 0,
    bytes_transferred INTEGER NOT NULL DEFAULT 0,
    resume_token TEXT UNIQUE,
    started_at DATETIME NOT NULL,
    ended_at DATETIME,
    last_activity_at DATETIME NOT NULL,
    FOREIGN KEY (file_id) REFERENCES files(file_id),
    FOREIGN KEY (room_id) REFERENCES rooms(room_id),
    FOREIGN KEY (server_id) REFERENCES backend_servers(server_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS transfer_chunks (
    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    processed_at DATETIME,
    FOREIGN KEY (transfer_id) REFERENCES file_transfers(transfer_id),
    UNIQUE (transfer_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS server_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id VARCHAR(20),
    user_id INTEGER,
    room_id INTEGER,
    event_type VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    ip_address VARCHAR(50),
    created_at DATETIME NOT NULL,
    FOREIGN KEY (server_id) REFERENCES backend_servers(server_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (room_id) REFERENCES rooms(room_id)
);

CREATE TABLE IF NOT EXISTS performance_metrics (
    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id INTEGER,
    user_id INTEGER,
    room_id INTEGER,
    server_id VARCHAR(20),
    metric_type VARCHAR(50) NOT NULL,
    value REAL NOT NULL,
    unit VARCHAR(20) NOT NULL,
    measured_at DATETIME NOT NULL,
    FOREIGN KEY (transfer_id) REFERENCES file_transfers(transfer_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (room_id) REFERENCES rooms(room_id),
    FOREIGN KEY (server_id) REFERENCES backend_servers(server_id)
);

CREATE TABLE IF NOT EXISTS message_reactions (
    reaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username VARCHAR(50) NOT NULL,
    emoji VARCHAR(10) NOT NULL,
    created_at DATETIME NOT NULL,
    FOREIGN KEY (message_id) REFERENCES room_messages(message_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE (message_id, user_id, emoji)
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_presence_status ON user_presence(status);
CREATE INDEX IF NOT EXISTS idx_rooms_server_id ON rooms(server_id);
CREATE INDEX IF NOT EXISTS idx_room_members_room_id ON room_members(room_id);
CREATE INDEX IF NOT EXISTS idx_room_messages_room_id_created_at ON room_messages(room_id, created_at);
CREATE INDEX IF NOT EXISTS idx_private_messages_pair ON private_messages(sender_id, recipient_id, created_at);
CREATE INDEX IF NOT EXISTS idx_files_room_id ON files(room_id);
CREATE INDEX IF NOT EXISTS idx_file_transfers_user_id ON file_transfers(user_id);
CREATE INDEX IF NOT EXISTS idx_transfer_chunks_transfer_id ON transfer_chunks(transfer_id);
CREATE INDEX IF NOT EXISTS idx_server_logs_created_at ON server_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_metrics_type ON performance_metrics(metric_type);

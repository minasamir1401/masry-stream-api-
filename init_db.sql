-- PostgreSQL Initialization Script for MasryStream Platform

CREATE TABLE IF NOT EXISTS genres (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    description VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS media (
    id SERIAL PRIMARY KEY,
    telegram_channel VARCHAR(128) NOT NULL,
    telegram_message_id INTEGER NOT NULL,
    telegram_watch_url VARCHAR(512),
    title VARCHAR(255) NOT NULL,
    original_title VARCHAR(255),
    description TEXT,
    release_year INTEGER,
    duration INTEGER,
    file_size BIGINT DEFAULT 0,
    mime_type VARCHAR(64) DEFAULT 'video/mp4',
    file_name VARCHAR(255),
    resolution VARCHAR(32),
    quality_label VARCHAR(32),
    poster_url VARCHAR(512),
    poster_file VARCHAR(255),
    media_type VARCHAR(16) DEFAULT 'MOVIE' NOT NULL,
    season_number INTEGER,
    episode_number INTEGER,
    parent_id INTEGER REFERENCES media(id) ON DELETE SET NULL,
    is_available BOOLEAN DEFAULT TRUE,
    direct_streamable BOOLEAN DEFAULT TRUE,
    view_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT uq_channel_message UNIQUE (telegram_channel, telegram_message_id)
);

CREATE TABLE IF NOT EXISTS media_genres (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    genre_id INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
    PRIMARY KEY (media_id, genre_id)
);

CREATE TABLE IF NOT EXISTS scrape_logs (
    id SERIAL PRIMARY KEY,
    channel VARCHAR(128) NOT NULL,
    source VARCHAR(16),
    last_scraped_message_id INTEGER DEFAULT 0,
    status VARCHAR(32) DEFAULT 'SUCCESS',
    items_found INTEGER DEFAULT 0,
    error_message TEXT,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- Performance Indexes
CREATE INDEX IF NOT EXISTS ix_media_title ON media(title);
CREATE INDEX IF NOT EXISTS ix_media_type ON media(media_type);
CREATE INDEX IF NOT EXISTS ix_media_channel ON media(telegram_channel);
CREATE INDEX IF NOT EXISTS ix_media_created ON media(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_media_parent ON media(parent_id);

CREATE TABLE IF NOT EXISTS cache_entries (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_cache_expires_at ON cache_entries(expires_at);

CREATE TABLE IF NOT EXISTS admin_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(256) NOT NULL,
    salt VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_admin_users_username ON admin_users(username);

CREATE TABLE IF NOT EXISTS visitor_sessions (
    id SERIAL PRIMARY KEY,
    visitor_id VARCHAR(64) UNIQUE NOT NULL,
    ip_hash VARCHAR(64),
    user_agent VARCHAR(255),
    first_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    duration_seconds INTEGER DEFAULT 0 NOT NULL,
    total_page_views INTEGER DEFAULT 0 NOT NULL,
    total_anime_browsed INTEGER DEFAULT 0 NOT NULL,
    total_anime_played INTEGER DEFAULT 0 NOT NULL,
    total_watch_seconds INTEGER DEFAULT 0 NOT NULL,
    last_path VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS ix_visitor_sessions_visitor_id ON visitor_sessions(visitor_id);

CREATE TABLE IF NOT EXISTS analytics_events (
    id SERIAL PRIMARY KEY,
    visitor_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    path VARCHAR(255) NOT NULL,
    anime_id INTEGER,
    anime_title VARCHAR(255),
    episode_number INTEGER,
    duration_seconds INTEGER DEFAULT 0 NOT NULL,
    referrer VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_analytics_events_visitor_id ON analytics_events(visitor_id);
CREATE INDEX IF NOT EXISTS ix_analytics_events_event_type ON analytics_events(event_type);
CREATE INDEX IF NOT EXISTS ix_analytics_events_created_at ON analytics_events(created_at);

CREATE TABLE IF NOT EXISTS anime_overrides (
    anime_id INTEGER PRIMARY KEY,
    is_hidden BOOLEAN DEFAULT FALSE NOT NULL,
    title VARCHAR(255),
    total_episodes INTEGER,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_anime_overrides_is_hidden ON anime_overrides(is_hidden);

CREATE TABLE IF NOT EXISTS custom_episodes (
    id SERIAL PRIMARY KEY,
    anime_id INTEGER NOT NULL,
    episode_number INTEGER NOT NULL,
    title VARCHAR(255),
    duration INTEGER,
    is_hidden BOOLEAN DEFAULT FALSE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT uq_custom_anime_ep UNIQUE (anime_id, episode_number)
);

CREATE INDEX IF NOT EXISTS ix_custom_episodes_anime_id ON custom_episodes(anime_id);
CREATE INDEX IF NOT EXISTS ix_custom_episodes_episode_number ON custom_episodes(episode_number);

CREATE TABLE IF NOT EXISTS custom_servers (
    id SERIAL PRIMARY KEY,
    anime_id INTEGER NOT NULL,
    episode_number INTEGER NOT NULL,
    name VARCHAR(128) NOT NULL,
    url TEXT NOT NULL,
    server_type VARCHAR(16) DEFAULT 'direct' NOT NULL,
    subtitle_url VARCHAR(512),
    order_index INTEGER DEFAULT 0 NOT NULL,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_custom_servers_anime_id ON custom_servers(anime_id);
CREATE INDEX IF NOT EXISTS ix_custom_servers_episode_number ON custom_servers(episode_number);



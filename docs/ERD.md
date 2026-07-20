# TV9 Bharatvarsh Social Media Publishing Platform
# Entity Relationship Document

Version: 1.0

---

# Database: PostgreSQL

---

# Table: roles

| Column | Type | Constraints |
|---------|------|-------------|
| id | BIGSERIAL | PK |
| name | VARCHAR(50) | UNIQUE |
| description | TEXT | |

---

# Table: users

| Column | Type | Constraints |
|---------|------|-------------|
| id | BIGSERIAL | PK |
| role_id | BIGINT | FK |
| name | VARCHAR(255) | NOT NULL |
| email | VARCHAR(255) | UNIQUE |
| password_hash | TEXT | NOT NULL |
| department | VARCHAR(100) | |
| is_active | BOOLEAN | DEFAULT TRUE |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

---

# Table: contents

| Column | Type | Constraints |
|---------|------|-------------|
| id | BIGSERIAL | PK |
| title | VARCHAR(500) | |
| description | TEXT | |
| caption | TEXT | |
| content_type | VARCHAR(50) | |
| file_url | TEXT | |
| thumbnail_url | TEXT | |
| status | VARCHAR(50) | |
| created_by | BIGINT | FK |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

---

# Table: content_tags

| Column | Type |
|---------|------|
| id | BIGSERIAL |
| content_id | BIGINT |
| tag | VARCHAR(100) |

---

# Table: platforms

| Column | Type |
|---------|------|
| id | BIGSERIAL |
| name | VARCHAR(50) |

Data:

1 → X

2 → Instagram

3 → Facebook

4 → YouTube

---

# Table: platform_accounts

| Column | Type |
|---------|------|
| id | BIGSERIAL |
| platform_id | BIGINT |
| account_name | VARCHAR(255) |
| access_token | TEXT |
| refresh_token | TEXT |
| token_expiry | TIMESTAMP |
| created_at | TIMESTAMP |

---

# Table: publish_jobs

| Column | Type |
|---------|------|
| id | BIGSERIAL |
| content_id | BIGINT |
| platform_id | BIGINT |
| status | VARCHAR(50) |
| scheduled_time | TIMESTAMP |
| published_time | TIMESTAMP |
| retry_count | INTEGER |
| platform_post_id | VARCHAR(255) |
| error_message | TEXT |
| created_at | TIMESTAMP |

---

# Table: approvals

| Column | Type |
|---------|------|
| id | BIGSERIAL |
| content_id | BIGINT |
| approved_by | BIGINT |
| status | VARCHAR(50) |
| comments | TEXT |
| approved_at | TIMESTAMP |

---

# Table: analytics

| Column | Type |
|---------|------|
| id | BIGSERIAL |
| publish_job_id | BIGINT |
| views | BIGINT |
| likes | BIGINT |
| shares | BIGINT |
| comments | BIGINT |
| reach | BIGINT |
| engagement_rate | NUMERIC(10,2) |
| last_synced | TIMESTAMP |

---

# Table: notifications

| Column | Type |
|---------|------|
| id | BIGSERIAL |
| user_id | BIGINT |
| message | TEXT |
| type | VARCHAR(50) |
| is_read | BOOLEAN |
| created_at | TIMESTAMP |

---

# Table: audit_logs

| Column | Type |
|---------|------|
| id | BIGSERIAL |
| user_id | BIGINT |
| action | VARCHAR(100) |
| entity_name | VARCHAR(100) |
| entity_id | BIGINT |
| old_value | JSONB |
| new_value | JSONB |
| ip_address | VARCHAR(100) |
| created_at | TIMESTAMP |

---

# Relationships

roles
1 ---- N users

users
1 ---- N contents

contents
1 ---- N content_tags

contents
1 ---- N publish_jobs

platforms
1 ---- N publish_jobs

platforms
1 ---- N platform_accounts

publish_jobs
1 ---- 1 analytics

contents
1 ---- N approvals

users
1 ---- N notifications

users
1 ---- N audit_logs

---

# ER Diagram

roles
│
└────── users
│
├────── contents
│
├────── approvals
│
├────── notifications
│
└────── audit_logs

contents
│
├────── content_tags
│
└────── publish_jobs
│
└────── analytics

platforms
│
├────── platform_accounts
│
└────── publish_jobs

---

# Recommended Indexes

users(email)

contents(status)

contents(created_by)

publish_jobs(status)

publish_jobs(scheduled_time)

analytics(last_synced)

audit_logs(user_id)

audit_logs(created_at)

content_tags(tag)
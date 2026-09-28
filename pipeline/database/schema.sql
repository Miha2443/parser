PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_metadata (
    version INTEGER PRIMARY KEY CHECK (version = 1)
) STRICT;
INSERT OR IGNORE INTO schema_metadata VALUES (1);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    scope TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS raw_files (
    raw_file_id TEXT PRIMARY KEY CHECK (length(raw_file_id) = 64),
    source_id TEXT NOT NULL REFERENCES sources,
    original_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    workbook_metadata_json TEXT NOT NULL CHECK (json_valid(workbook_metadata_json)),
    registered_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS dataset_versions (
    version_id TEXT PRIMARY KEY,
    dataset TEXT NOT NULL CHECK (dataset = 'monitoring'),
    raw_file_id TEXT NOT NULL REFERENCES raw_files,
    normalizer_version TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK (record_count > 0),
    records_sha256 TEXT NOT NULL,
    validation_json TEXT NOT NULL CHECK (json_valid(validation_json)),
    created_at TEXT NOT NULL,
    UNIQUE (raw_file_id, normalizer_version),
    UNIQUE (version_id, raw_file_id)
) STRICT;

-- Identity is the physical source row within an immutable version. UIN is
-- nullable and nonunique: permit components and buildings must not be merged.
-- Area values are canonical decimal TEXT, never SQLite REAL. The repository
-- registers decimal_sum, which sums using Decimal rather than SQLite SUM.
CREATE TABLE IF NOT EXISTS monitoring_records (
    version_id TEXT NOT NULL,
    raw_file_id TEXT NOT NULL,
    source_sheet TEXT NOT NULL,
    source_row INTEGER NOT NULL CHECK (source_row >= 2),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) = 64),
    registry TEXT NOT NULL CHECK (registry IN ('rv', 'oks')),
    uin TEXT,
    developer TEXT,
    object_name TEXT,
    address TEXT,
    okrug TEXT,
    district TEXT,
    permit_number TEXT,
    grouping TEXT,
    purpose TEXT,
    funding_source TEXT,
    year INTEGER,
    total_area_m2 TEXT,
    residential_area_m2 TEXT,
    category_residential_m2 TEXT NOT NULL,
    category_common_m2 TEXT NOT NULL,
    category_nonres_in_res_m2 TEXT NOT NULL,
    category_nonres_other_m2 TEXT NOT NULL,
    raw_total_area_missing INTEGER NOT NULL CHECK (raw_total_area_missing IN (0, 1)),
    raw_residential_area_missing INTEGER NOT NULL CHECK (raw_residential_area_missing IN (0, 1)),
    raw_total_area_invalid INTEGER NOT NULL CHECK (raw_total_area_invalid IN (0, 1)),
    raw_residential_area_invalid INTEGER NOT NULL CHECK (raw_residential_area_invalid IN (0, 1)),
    normalized_payload_json TEXT NOT NULL CHECK (json_valid(normalized_payload_json)),
    raw_payload_json TEXT NOT NULL CHECK (json_valid(raw_payload_json)),
    PRIMARY KEY (version_id, raw_file_id, source_sheet, source_row),
    UNIQUE (version_id, source_record_id),
    FOREIGN KEY (version_id, raw_file_id) REFERENCES dataset_versions(version_id, raw_file_id)
        DEFERRABLE INITIALLY DEFERRED
) STRICT;
CREATE INDEX IF NOT EXISTS monitoring_developer ON monitoring_records(version_id, developer, registry, year);
CREATE INDEX IF NOT EXISTS monitoring_year ON monitoring_records(version_id, registry, year);
CREATE INDEX IF NOT EXISTS monitoring_uin ON monitoring_records(version_id, uin);

CREATE TABLE IF NOT EXISTS publications (
    publication_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS publication_members (
    publication_id TEXT NOT NULL REFERENCES publications DEFERRABLE INITIALLY DEFERRED,
    dataset TEXT NOT NULL CHECK (dataset = 'monitoring'),
    version_id TEXT NOT NULL REFERENCES dataset_versions,
    PRIMARY KEY (publication_id, dataset)
) STRICT;
CREATE TABLE IF NOT EXISTS active_publication (
    slot TEXT PRIMARY KEY CHECK (slot = 'monitoring'),
    publication_id TEXT REFERENCES publications,
    generation INTEGER NOT NULL CHECK (generation >= 0)
) STRICT;
INSERT OR IGNORE INTO active_publication VALUES ('monitoring', NULL, 0);

-- Records are inserted before the version metadata using the deferred FK.
-- Inserting the metadata seals the version in the same transaction.
CREATE TRIGGER IF NOT EXISTS monitoring_no_late_insert
BEFORE INSERT ON monitoring_records
WHEN EXISTS (SELECT 1 FROM dataset_versions WHERE version_id = NEW.version_id)
BEGIN SELECT RAISE(ABORT, 'version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS monitoring_no_update BEFORE UPDATE ON monitoring_records
BEGIN SELECT RAISE(ABORT, 'records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS monitoring_no_delete BEFORE DELETE ON monitoring_records
BEGIN SELECT RAISE(ABORT, 'records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS versions_no_update BEFORE UPDATE ON dataset_versions
BEGIN SELECT RAISE(ABORT, 'versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS versions_no_replace BEFORE INSERT ON dataset_versions
WHEN EXISTS (SELECT 1 FROM dataset_versions WHERE version_id = NEW.version_id
             OR (raw_file_id = NEW.raw_file_id AND normalizer_version = NEW.normalizer_version))
BEGIN SELECT RAISE(ABORT, 'versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS versions_no_delete BEFORE DELETE ON dataset_versions
BEGIN SELECT RAISE(ABORT, 'versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS raw_no_update BEFORE UPDATE ON raw_files
BEGIN SELECT RAISE(ABORT, 'raw files are immutable'); END;
CREATE TRIGGER IF NOT EXISTS raw_no_replace BEFORE INSERT ON raw_files
WHEN EXISTS (SELECT 1 FROM raw_files WHERE raw_file_id = NEW.raw_file_id)
BEGIN SELECT RAISE(ABORT, 'raw files are immutable'); END;
CREATE TRIGGER IF NOT EXISTS raw_no_delete BEFORE DELETE ON raw_files
BEGIN SELECT RAISE(ABORT, 'raw files are immutable'); END;
CREATE TRIGGER IF NOT EXISTS sources_no_replace BEFORE INSERT ON sources
WHEN EXISTS (SELECT 1 FROM sources WHERE source_id = NEW.source_id)
BEGIN SELECT RAISE(ABORT, 'sources are immutable'); END;
CREATE TRIGGER IF NOT EXISTS sources_no_update BEFORE UPDATE ON sources
BEGIN SELECT RAISE(ABORT, 'sources are immutable'); END;
CREATE TRIGGER IF NOT EXISTS sources_no_delete BEFORE DELETE ON sources
BEGIN SELECT RAISE(ABORT, 'sources are immutable'); END;
CREATE TRIGGER IF NOT EXISTS members_no_late_insert BEFORE INSERT ON publication_members
WHEN EXISTS (SELECT 1 FROM publications WHERE publication_id = NEW.publication_id)
BEGIN SELECT RAISE(ABORT, 'publication is immutable'); END;
CREATE TRIGGER IF NOT EXISTS members_no_update BEFORE UPDATE ON publication_members
BEGIN SELECT RAISE(ABORT, 'publication members are immutable'); END;
CREATE TRIGGER IF NOT EXISTS members_no_delete BEFORE DELETE ON publication_members
BEGIN SELECT RAISE(ABORT, 'publication members are immutable'); END;
CREATE TRIGGER IF NOT EXISTS publications_no_update BEFORE UPDATE ON publications
BEGIN SELECT RAISE(ABORT, 'publications are immutable'); END;
CREATE TRIGGER IF NOT EXISTS publications_no_replace BEFORE INSERT ON publications
WHEN EXISTS (SELECT 1 FROM publications WHERE publication_id = NEW.publication_id)
BEGIN SELECT RAISE(ABORT, 'publications are immutable'); END;
CREATE TRIGGER IF NOT EXISTS publications_no_delete BEFORE DELETE ON publications
BEGIN SELECT RAISE(ABORT, 'publications are immutable'); END;

-- 0002_incident_search.sql
-- Additive: full-text search over incidents. Existing rows are back-filled,
-- no table is dropped or rewritten.

CREATE VIRTUAL TABLE incident_search USING fts5(
    incident_id UNINDEXED, camera_id, primary_reason, reasons, detection_classes, notes, video_ref,
    tokenize = 'unicode61'
);

INSERT INTO incident_search (incident_id, camera_id, primary_reason, reasons, detection_classes, notes, video_ref)
SELECT id, camera_id, coalesce(primary_reason, ''), reasons, detection_classes, coalesce(notes, ''), coalesce(video_ref, '')
FROM incidents;

CREATE TRIGGER incidents_ai AFTER INSERT ON incidents BEGIN
    INSERT INTO incident_search (incident_id, camera_id, primary_reason, reasons, detection_classes, notes, video_ref)
    VALUES (new.id, new.camera_id, coalesce(new.primary_reason, ''), new.reasons, new.detection_classes,
            coalesce(new.notes, ''), coalesce(new.video_ref, ''));
END;

CREATE TRIGGER incidents_au AFTER UPDATE ON incidents BEGIN
    DELETE FROM incident_search WHERE incident_id = old.id;
    INSERT INTO incident_search (incident_id, camera_id, primary_reason, reasons, detection_classes, notes, video_ref)
    VALUES (new.id, new.camera_id, coalesce(new.primary_reason, ''), new.reasons, new.detection_classes,
            coalesce(new.notes, ''), coalesce(new.video_ref, ''));
END;

CREATE TRIGGER incidents_ad AFTER DELETE ON incidents BEGIN
    DELETE FROM incident_search WHERE incident_id = old.id;
END;

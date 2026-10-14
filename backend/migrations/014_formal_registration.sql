-- R10 §6: preserve display identity and the administrator of each version.
ALTER TABLE harness_registrations ADD COLUMN name TEXT;
ALTER TABLE harness_registrations ADD COLUMN registered_by TEXT;
ALTER TABLE task_attempts ADD COLUMN registration_version TEXT;

-- Runs once on an empty data volume. The shop database ("shop") is created by the image from
-- POSTGRES_DB; the control plane (deployer + chaos controller) gets its own database.
CREATE DATABASE simcontrol;

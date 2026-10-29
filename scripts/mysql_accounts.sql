-- Run using the existing local administrator login on MySQL 8.0.28.
-- partial_revokes=OFF was checked for this setup; backslashes make underscores literal.
-- CREATE USER returns generated passwords. Keep that output private.
-- These names were checked as unused. Rerunning never resets existing passwords.
CREATE USER 'termslens'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD;
CREATE USER 'termslens_test'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD;

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES
ON `termslens\_db`.* TO 'termslens'@'127.0.0.1';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES
ON `termslens\_test`.* TO 'termslens_test'@'127.0.0.1';

SHOW GRANTS FOR 'termslens'@'127.0.0.1';
SHOW GRANTS FOR 'termslens_test'@'127.0.0.1';

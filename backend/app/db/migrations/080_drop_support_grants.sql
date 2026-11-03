-- Support grants let a staff member read one student's chat or artifact for a
-- support case. The product no longer offers it: feedback triage already shows
-- the answer a student flagged, and reading student chats is the largest
-- privacy surface in the app. The three tables go with the routes.
DROP TABLE IF EXISTS granted_result_staging;
DROP TABLE IF EXISTS support_grant_requests;
DROP TABLE IF EXISTS resource_grants;

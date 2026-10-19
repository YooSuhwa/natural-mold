CREATE ROLE app_user LOGIN PASSWORD 'pw' NOSUPERUSER NOBYPASSRLS;
CREATE TABLE agents (id serial primary key, tenant_id uuid not null, name text not null);
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON agents
  USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
         OR current_setting('app.scope', true) = 'platform')
  WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
         OR current_setting('app.scope', true) = 'platform');
GRANT SELECT, INSERT, UPDATE, DELETE ON agents TO app_user;
GRANT USAGE ON SEQUENCE agents_id_seq TO app_user;
INSERT INTO agents (tenant_id, name) VALUES
 ('aaaaaaaa-0000-0000-0000-000000000001','a1'),('aaaaaaaa-0000-0000-0000-000000000001','a2'),
 ('bbbbbbbb-0000-0000-0000-000000000002','b1');

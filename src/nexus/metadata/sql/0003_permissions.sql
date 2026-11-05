-- PULSO NEXUS E1-B: permisos de mínimo privilegio para el rol de aplicación.
-- El productor solo ejecuta procedimientos (encadenamiento de propiedad dbo -> nexus).
IF DATABASE_PRINCIPAL_ID(N'nexus_producer_role') IS NULL
    CREATE ROLE nexus_producer_role AUTHORIZATION dbo;
GO
GRANT EXECUTE ON SCHEMA::nexus TO nexus_producer_role;
GO
-- Nunca DENY CONTROL: CONTROL implica EXECUTE y bloquearía los propios procedimientos
-- (defecto detectado en CI real, error 229 sobre usp_get_environment).
DENY SELECT, INSERT, UPDATE, DELETE, ALTER, REFERENCES, TAKE OWNERSHIP ON SCHEMA::nexus TO nexus_producer_role;
GO
DENY CREATE TABLE, CREATE PROCEDURE, CREATE VIEW, CREATE FUNCTION, CREATE SCHEMA TO nexus_producer_role;
GO

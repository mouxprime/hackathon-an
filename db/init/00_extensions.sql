-- Extensions Postgres requises au démarrage du conteneur (boot FRAIS uniquement :
-- ce script n'est rejoué que si le volume pgdata est vide — sur un volume existant,
-- le seed refait les mêmes CREATE EXTENSION IF NOT EXISTS au runtime).
-- Les tables, elles, sont créées par SQLAlchemy (init_db, idempotent) :
-- les modèles ORM sont la source de vérité unique du schéma.
CREATE EXTENSION IF NOT EXISTS vector;
-- Recherche floue sur les noms de députés et titres de scrutins/dossiers (index GIN trgm).
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- Normalisation sans accents côté requêtes ad hoc (les colonnes *_normalise sont,
-- elles, remplies en Python au seed — pas d'index fonctionnel sur unaccent()).
CREATE EXTENSION IF NOT EXISTS unaccent;

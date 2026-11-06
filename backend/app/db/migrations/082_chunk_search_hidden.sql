-- A course that runs every term has one near-identical Atlas result card per term,
-- and those cards fill the search candidate pool before ranking starts. A hidden
-- chunk stays readable and citable; only the keyword and embedding seams skip it.
ALTER TABLE chunks ADD COLUMN search_hidden boolean NOT NULL DEFAULT false;

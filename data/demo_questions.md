# Mycelium demo questions

Four beats for the live demo. Each beat names the citation the answer should land on so a judge can follow along.

## BEAT1: a crisp how-to

Question: "How do I run SurrealDB with Docker?"

Expected: start the server with `docker run --rm --pull always -p 8000:8000 surrealdb/surrealdb:latest start`. SurrealDB listens on port 8000 by default in the container, and you add a `-v` mount plus an on-disk engine such as `rocksdb:/mydata/mydatabase.db` when the data must persist across restarts. Citations should point at the Docker reference page and the `start` command usage in it.

Why it lands: this is a single-pass lookup with one obvious source, so it shows the plain retrieval and citation path before any correction is needed.

## BEAT2: a genuinely ambiguous question

Question: "How do I give someone access?"

This is genuinely ambiguous because "someone" and "access" each have more than one meaning in SurrealDB. Three readings are all defensible:

- Literal reading: give a person a root system user so they can operate the server. This is the `--user` and `--pass` arguments on `start`, which persist a top level user named `root`, or a `DEFINE USER ... ON ROOT` statement. The source for this is the Docker page and the `DEFINE USER` page.
- Intent reading: give an end user of the application a record user they can sign in as. This is `DEFINE ACCESS ... ON DATABASE TYPE RECORD` with `SIGNUP` and `SIGNIN` expressions, which is what makes SurrealDB act as a web database. The source is the record access page and the `DEFINE ACCESS` page.
- Scope reading: give record users permission over records in a specific table. This is the table `PERMISSIONS` clause with `FOR select`, `FOR create`, `FOR update` and `FOR delete`, plus field `PERMISSIONS` for individual fields. The source is the `DEFINE TABLE` page and the permissions and row level security page.

Reading to tap as the correction: the intent reading, a record user defined with `DEFINE ACCESS ... TYPE RECORD` whose reach is then narrowed by table `PERMISSIONS`.

Why: the question says "someone", which in an application means an end user rather than the operator who holds the root credential. The root reading is the literal one, but it is the wrong default for a product that signs end users in. Once the system taps the record user reading, it should pair it with the table `PERMISSIONS` reading, because creating the access method alone grants a sign in but no rights over any table.

## BEAT3: a paraphrase whose right reading is that one

Question: "A customer of ours should only ever see their own orders when they log in. How do I set that up?"

Right reading: the same one tapped in BEAT2, a record user plus row level rules. The answer should combine `DEFINE ACCESS ... ON DATABASE TYPE RECORD` for the sign in with a table `PERMISSIONS FOR select WHERE customer = $auth.id` rule, since `$auth` holds the current identity after sign in. A paraphrase like this shows the correction transfers: the wording no longer looks like the BEAT2 wording, but it resolves to the record user reading rather than the root user reading.

## UPLOAD: one question per uploaded file

- PDF (DEFINE ANALYZER page): "According to the uploaded analyzer PDF, what does an analyzer do before text can be searched?" The expected answer is that an analyzer processes text so that it can be searched, and the PDF should be cited as the source.
- Spreadsheet (surrealdb-reference.xlsx): "What example does the spreadsheet give for the datetime data type?" The expected answer is the literal datetime example listed in the Data types sheet, and the sheet should be cited as the source.

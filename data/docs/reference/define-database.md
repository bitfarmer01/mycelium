# DEFINE DATABASE

source_link: https://surrealdb.com/docs/reference/query-language/statements/define/database

# `DEFINE DATABASE` statement

The `DEFINE DATABASE` statement allows you to instantiate a named database, enabling you to specify security and configuration options.

## Requirements

- You must be authenticated as a root owner or editor, or namespace owner or editor before you can use the `DEFINE DATABASE` statement.
- [You must select your namespace](/docs/reference/query-language/statements/use) before you can use the `DEFINE DATABASE` statement.

## Statement syntax

 
**SurrealQL Syntax**

```syntax title="SurrealQL Syntax"
DEFINE DATABASE [ OVERWRITE | IF NOT EXISTS ] @name [ STRICT ] [ COMMENT @string ]
```

 
**Railroad Diagram**

## Example usage
Below shows how you can create a database using the DEFINE DATABASE statement.

```surql
/**[test]

[[test.results]]
value = "{ database: 'test', namespace: 'abcum' }"

[[test.results]]
value = "NONE"

*/

-- Specify the namespace for the database
USE NS abcum;

-- Define database
DEFINE DATABASE app_vitalsense;
```

## Using `IF NOT EXISTS` clause

The `IF NOT EXISTS` clause can be used to define a database only if it does not already exist. You should use the `IF NOT EXISTS` clause when defining a database in SurrealDB if you want to ensure that the database is only created if it does not already exist. If the database already exists, the `DEFINE DATABASE` statement will return an error.

It's particularly useful when you want to safely attempt to define a database without manually checking its existence first.

On the other hand, you should not use the `IF NOT EXISTS` clause when you want to ensure that the database definition is updated regardless of whether it already exists. In such cases, you might prefer using the `OVERWRITE` clause, which allows you to define a database and overwrite an existing one if it already exists, ensuring that the latest version of the definition is always in use.

```surql
/**[test]

[[test.results]]
value = "NONE"

*/

-- Create a database if it does not already exist
DEFINE DATABASE IF NOT EXISTS app_vitalsense;
```

## Using `OVERWRITE` clause

The `OVERWRITE` clause can be used to define a database and overwrite an existing one if it already exists. You should use the `OVERWRITE` clause when you want to modify an existing database definition. If the database already exists, the `DEFINE DATABASE` statement will overwrite the existing definition with the new one.

```surql
/**[test]

[[test.results]]
value = "NONE"

*/

-- Create a database and overwrite if it already exists
DEFINE DATABASE OVERWRITE app_vitalsense;
```

## Defining a `STRICT` database

A strict database is one that does not allow a resource to be used unless it has already been defined. The default behaviour in SurrealDB works otherwise, by allowing statements like [CREATE](/docs/reference/query-language/statements/create), [INSERT](/docs/reference/query-language/statements/insert) and [UPSERT](/docs/reference/query-language/statements/create) to work.

```surql
CREATE some_new_table;
INFO FOR DATABASE.tables;
```

The output of the [INFO](/docs/reference/query-language/statements/info) statement shows that a table called `some_new_table` has been created with a few default clauses.

```surql
{
 some_new_table: 'DEFINE TABLE some_new_table TYPE ANY SCHEMALESS PERMISSIONS NONE'
}
```

Such an operation within a strict database is simply not allowed.

```surql
DEFINE DATABASE new_db STRICT;
USE DATABASE new_db;
CREATE some_new_table;
```

```surql title="Output"
"The table 'some_new_table' does not exist"
```

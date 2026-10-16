# KILL

source_link: https://surrealdb.com/docs/reference/query-language/statements/kill

# `KILL` statement

The `KILL` statement is used to terminate a running live query.

While the `KILL` statement does accept a value type, this value must resolve to a UUID. Consequently, it will accept a string literal of a UUID or a param.

## Statement syntax

 
**SurrealQL Syntax**

```syntax title="SurrealQL Syntax"
KILL @value;
```

 
**Railroad Diagram**

## Example usage

### Basic usage

The `KILL` statement expects the UUID of a running [live select](/docs/reference/query-language/statements/live-select) query to be passed. This UUID can be found in the output of the `LIVE` statement, and can thereafter be passed into a `KILL` statement once it is no longer needed.

```surql
-- Possible output, as every live query is given its own id
LIVE SELECT DIFF FROM person;
//- u'0189d6e3-8eac-703a-9a48-d9faa78b44b9'

-- Some time later...
KILL u"0189d6e3-8eac-703a-9a48-d9faa78b44b9";
```

The `KILL` statement also allows for parameters to be used.

```surql
-- Define the parameter
LET $live_query_id = u"0189d6e3-8eac-703a-9a48-d9faa78b44b9";
-- Use the parameter
KILL $live_query_id;
```

Using the `KILL` statement on a UUID that does not correspond to a running live query will generate an error.

```surql
LET $rand = rand::uuid();
KILL $rand;
KILL u'9276b05b-e59a-49cd-9dd1-17c6fd15c28f';
```

```surql title="Output"
"Can not execute KILL statement using id '$rand'"
"Can not execute KILL statement using id 'u'9276b05b-e59a-49cd-9dd1-17c6fd15c28f''"
```

## Kill notifications

A separate notification is sent out when a `KILL` statement is enacted on a live query ID.

```surql
-- Possible output, as every live query is given its own id
LIVE SELECT * FROM person;
//- u'cf447091-9463-4d75-b32a-08513eb2a07c'

KILL u'cf447091-9463-4d75-b32a-08513eb2a07c';
```

```surql title="Output"
-- Query 1
NONE

-- Notification (action: Killed, live query ID: cf447091-9463-4d75-b32a-08513eb2a07c)
NONE
```

# Test sites

Five small web apps the agent is tested against. Each is built to have a different shape, and each has a hidden answer key
(`expected.json`, never served by the site) and a `grade.py` that scores what the agent saved.

| Folder | Site | Shape | Port | Needs |
|---|---|---|---|---|
| `ap-desk/` | AP Desk | invoice matching across four pages, then a form with computed values | 5180 | Node only |
| `returns-desk/` | Returns Desk | paging with no search, a branching route, a long policy page, a custom calendar | 5181 | Node only |
| `expense-desk/` | Expense Desk | a single-page app: hash routes, tabs, collapsed sections, a three-step wizard | 5182 | Node only |
| `claims-desk/` | Claims Desk | real-web hazards: sign-in, cookie banner, popup, endless list, form inside an iframe | 5183 | Node only |
| `credit-desk/` | Credit Desk | a ledger lookup and an Approve / Reject / Hold decision | 5174 | a Supabase project (see its README) |

## Run one

```bash
cd testsites/ap-desk
node server.js                 # no dependencies; PORT=... to change the port
python3 grade.py 10            # scores the first 10 records after a run
curl -X POST localhost:5180/admin/reset   # clears the decisions between runs
```

`./start.sh` starts the four Node sites together (`./start.sh stop` stops them). The Node sites keep their state in
`data/state.json`, which is git-ignored and created on first use.

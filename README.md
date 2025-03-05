# Solana-Bot



 Known Issues
Rate Limit Exceeded:

API requests exceed the limit when left running for too long.
🔧 Fix: Implement request throttling (API limit is ~300/min, but check the docs).


Database Optimization Needed:

Queries could slow down as more tokens are stored.
🔧 Fix: Index critical columns (contract_address, market_cap) for faster lookups.


Get new metrics and parameters:

Examples:
- volume
- TXNs
- specific metrics after a certain time(e.g. metrics 5 mins, 10 mins, 20 mins, 30 mins, 45 mins, 1 hour, 1hour 15 mins, 1hour 30 mins, 2 hours, after dex is paid.
- Save data on if boosts are paid (not a key metric but worth noting)
- look into other metrics too.

Look into the data offered in other APIs - bitQuery.

Think about how to store historical data

Big one, is the initial "DEX PAID MARKET CAP", actually this? Or is it just first market cap recorded? This needs checking, and if incrrect, it needs fixing.


check that the bot is tracking changes through the right api end points (speed things up), searching for token-- i dont think this i soptimised. A lot of time a requests wasted. I think we could use a separate bot to track changes?? Uses CA from data base to track changes. Only tracks coins for up to 2-3 hours after dec is paid


Need to add a filter for minimum MC

The bots output is only useful for a few minutes before it stops producing good output (just checks tokens, just isnt updating saved ones, nor detecting new ones). Needs looking into.

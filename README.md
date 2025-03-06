# Solana-Bot



 Known Issues
Rate Limit Exceeded:

API requests exceed the limit when left running for too long.
🔧 Fix: Implement request throttling (API limit is ~300/min, but check the docs).


Database Optimization Needed:

Queries could slow down as more tokens are stored.
🔧 Fix: Index critical columns (contract_address, market_cap) for faster lookups.


- 

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
**I can confirm that it isnt saving the dex paid market cap, it is saving the market cap of the token when first seen. We will need to use a differnt api end point to get the correct dex paid MC.


check that the bot is tracking changes through the right api end points (speed things up), searching for token-- i dont think this i soptimised. A lot of time a requests wasted. I think we could use a separate bot to track changes?? Uses CA from data base to track changes. Only tracks coins for up to 2-3 hours after dec is paid

Add an api which can add the price of SOL at the time dex paid

Need to add a filter for minimum MC

The bots output is only useful for a few minutes before it stops producing good output (just checks tokens, just isnt updating saved ones, nor detecting new ones). Needs looking into.



Second Bot (autonomous)

this bot will do a lot more. Live trading, ambiguious patterns.
Will have to do a lot fo research into this before buidling it
Will take advantages of the market micro strutures of dex paid tokens ( this hasnt been done before). These will be discovered when modelling using the data collected by the first bot. (Dissertation project)

Metrics: 
- will use performance metrics such as RSI
- Will take into account market sentiment
- will need data by the micro second.

Custom sniper bot will be set up to use at this point.
- 

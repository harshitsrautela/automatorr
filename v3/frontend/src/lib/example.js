// A ready-made task for the Credit Desk test site, so a first run needs no typing.

export const CREDIT_DESK_PROMPT = `Go to http://localhost:5174. You work at a credit desk: the home page lists retailers asking for goods on credit.

For the request whose number is the record's id: find that row (use Next if it is not on the first page). Note the retailer name, the city and the amount requested.

Open the Retailer Ledger page and find the retailer with exactly the same name AND the same city (use the search box; the same name can exist in several cities, and a name that is only similar does not count). Note its status, credit term (days), credit limit, outstanding amount and days since last payment.

Decide:
- Retailer is not in the ledger: Hold
- Retailer is Blocked: Reject
- Days since last payment is greater than the credit term: Reject
- Outstanding + amount requested is at or under the credit limit: Approve
- Over the limit, but by no more than 10% of the limit: Hold
- Otherwise: Reject

Go back to the Credit Requests page, find that request's row again, and click exactly one button for it: the tick for Approve, the cross for Reject, the dash for Hold. Finish by saying which request you decided, the numbers you used and your decision.`;

// Records for the example: request numbers 1 to 100 (the test site has 100 requests).
export const CREDIT_DESK_DATA = Array.from({ length: 100 }, (_, i) => ({ id: i + 1 }));

// The accuracy check for the Credit Desk site: compare the decision column with the correct-answer view.
export const CREDIT_DESK_VERIFY = {
  verify_kind: "expected_view",
  verify_config: {
    table: "credit_requests",
    key_column: "id",
    value_column: "decision",
    expected_view: "credit_requests_expected",
    expected_key_column: "request_id",
    expected_value_column: "expected_decision",
  },
};

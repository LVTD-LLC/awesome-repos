# Greptile Review Rules

- In `PRODUCT.md`, the `## Register` section is intentionally a single bare value: `product` or `brand`. Treat that as a design-tooling contract, not as placeholder text.
- For list-request modal reviews, account for the hidden `next` field and `AwesomeListRequestView.get_success_url()` before flagging the success redirect target.

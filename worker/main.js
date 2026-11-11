// The paper Worker's entry point on Cloudflare (paper/wrangler.jsonc). It adds,
// to the fetch and cron handlers in worker/index.js, the EmailEvents entrypoint
// mailroom calls with this paper's bounces and complaints (worker/email.js).
// worker/index.js stays free of cloudflare:* imports, so the tests run on Node.
import { WorkerEntrypoint } from "cloudflare:workers";
import worker from "./index.js";
import { recordEmailEvent } from "./email.js";

export class EmailEvents extends WorkerEntrypoint {
  async onEmailEvent(event) {
    return recordEmailEvent(this.env, event);
  }
}

export default worker;

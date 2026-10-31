/* jscpd:ignore-start -- Question and Approval intentionally own independent pending-settlement lifecycles. */
function settlePendingComposer(settle, failureMessage) {
    try {
        settle();
        return Promise.resolve();
    }
    catch (error) {
        return Promise.reject(error instanceof Error
            ? error
            : new Error(failureMessage, { cause: error }));
    }
}
/**
 * Narrow a request to a renderable plan review, or return undefined to leave it
 * to the generic question flow.
 *
 * The card offers approval and a return to the composer for change requests.
 * It accepts one question carrying the plan as detail and the named approve
 * option, with at most one alternative and no multi-select. Larger choices
 * remain in the generic question flow.
 *
 * @param questions - the request's whole question batch.
 * @returns The narrowed review, or undefined when the generic flow owns it.
 */
export function planReviewOf(questions) {
    if (questions.length !== 1)
        return undefined;
    // Length-checked above; the index read is the narrowing tax, not a guess.
    const question = questions[0];
    const intent = question.intent;
    if (intent?.kind !== 'plan-review' || question.detail === undefined)
        return undefined;
    if (question.multiSelect === true)
        return undefined;
    const options = question.options ?? [];
    if (options.length > 2)
        return undefined;
    const approve = options.find(option => option.label === intent.approve);
    if (approve === undefined)
        return undefined;
    const decline = options.find(option => option.label !== intent.approve);
    return {
        id: question.id,
        question: question.question,
        plan: question.detail,
        ...(intent.callId === undefined ? {} : { callId: intent.callId }),
        approve,
        ...(decline === undefined ? {} : { decline }),
    };
}
let nextQuestionKey = 0;
/** Create a wire-preserved user-question rejection. */
function questionError(message, code) {
    const error = new Error(message);
    error.name = 'UserQuestionError';
    error.code = code;
    return error;
}
/** One answerable Client presentation of a pending Host waterfall. */
export class PendingQuestion {
    sessionId;
    /** Presentation discriminator used by Session pending-interaction consumers. */
    kind;
    /** Opaque render identity and request key for the Session-scoped draft store. */
    key;
    /** The request's question list. */
    questions;
    /** Result returned by the Remote Event listener to the Host waterfall. */
    result;
    #resolve;
    #reject;
    #signal;
    #onAbort;
    #delegated = Symbol('pending question delegated');
    #settled = false;
    /**
     * @param sessionId - Agent/Session identity owning the scoped request.
     * @param questions - complete question batch.
     * @param signal - Host request and delivery lifetime.
     */
    constructor(sessionId, questions, signal) {
        this.sessionId = sessionId;
        nextQuestionKey += 1;
        this.key = `question:${String(nextQuestionKey)}`;
        this.questions = questions;
        this.kind = planReviewOf(questions) === undefined ? 'question' : 'plan-review';
        const completion = Promise.withResolvers();
        this.result = completion.promise;
        this.#resolve = completion.resolve;
        this.#reject = completion.reject;
        this.#signal = signal;
        if (signal === undefined) {
            this.#onAbort = undefined;
            return;
        }
        const onAbort = () => {
            this.abort(questionError('ask_user_question was aborted before the user answered', 'ASK_ABORTED'));
        };
        this.#onAbort = onAbort;
        signal.addEventListener('abort', onAbort, { once: true });
        if (signal.aborted)
            onAbort();
    }
    /**
     * Resolve the Host waterfall with the whole answer batch.
     * @param answer - complete structured answer batch.
     */
    answer(answer) {
        return settlePendingComposer(() => {
            this.finish(() => { this.#resolve(answer); });
        }, 'pending question settlement failed');
    }
    /** Delegate an unanswered request to the next waterfall listener. */
    delegate() {
        if (this.#settled)
            return;
        this.finish(() => { this.#reject(this.#delegated); });
    }
    /**
     * Test whether a rejection requests waterfall delegation.
     * @param reason - rejection received from {@link PendingQuestion.result}.
     * @returns whether {@link PendingQuestion.delegate} produced it.
     */
    isDelegation(reason) {
        return reason === this.#delegated;
    }
    /** Reject the Host waterfall because the user closed the question. */
    cancel() {
        return settlePendingComposer(() => {
            this.finish(() => {
                this.#reject(questionError('the user cancelled ask_user_question', 'ASK_CANCELLED'));
            });
        }, 'pending question cancellation failed');
    }
    /**
     * End an unanswered presentation when its transport, scope, or plugin lifetime ends.
     * @param reason - rejection exposed to the waiting Remote Event listener.
     */
    abort(reason) {
        if (this.#settled)
            return;
        this.finish(() => { this.#reject(reason); });
    }
    finish(settle) {
        if (this.#settled)
            throw new Error(`pending question ${this.key} is already settled`);
        this.#settled = true;
        if (this.#signal !== undefined && this.#onAbort !== undefined) {
            this.#signal.removeEventListener('abort', this.#onAbort);
        }
        settle();
    }
}
//# sourceMappingURL=slots.js.map
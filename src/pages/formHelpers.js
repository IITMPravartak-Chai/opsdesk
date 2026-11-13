// Small pieces shared by forms whose state lives in a reducer. Pure, so `node --test` covers them.

/** An onChange handler that reports a field's new value to the form's reducer as { type: "field", key, value }. */
export const onField = (dispatch, key) => (event) => dispatch({ type: "field", key, value: event.target.value });

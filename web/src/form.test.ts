// What an untouched form submits for optional fields. The schema is the dashboard
// server's own normalize_for_form output (a Python test pins the fixture to it);
// here RJSF itself decides the starting values, and AJV validates them.

import { getDefaultFormState } from "@rjsf/utils";
import type { RJSFSchema } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import { expect, test } from "vitest";
import raw from "./__fixtures__/nullable-form.schema.json";

const schema = raw as RJSFSchema;

test("an untouched form leaves every optional field null or unset", () => {
  const start = getDefaultFormState(validator, schema, undefined, schema) as Record<
    string,
    unknown
  >;
  for (const field of ["denoise", "mode", "only", "pair", "color", "pet", "seed"]) {
    expect([null, undefined]).toContain(start[field]);
  }
});

test("the untouched form validates, so it can be submitted", () => {
  const start = getDefaultFormState(validator, schema, undefined, schema);
  const { errors } = validator.validateFormData(start, schema);
  expect(errors).toEqual([]);
});

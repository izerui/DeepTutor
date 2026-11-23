import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const changeOwnPassword = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: "en" } }),
}));
vi.mock("@/lib/profile-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/profile-api")>("@/lib/profile-api")),
  changeOwnPassword: (...args: unknown[]) => changeOwnPassword(...args),
}));

import { PasswordCard } from "@/components/profile/PasswordCard";

function fill(current: string, next: string, confirm: string) {
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: current } });
  fireEvent.change(screen.getByLabelText("New password (at least 8 characters)"), {
    target: { value: next },
  });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: confirm } });
}

describe("PasswordCard", () => {
  beforeEach(() => {
    changeOwnPassword.mockReset();
    changeOwnPassword.mockResolvedValue(undefined);
  });

  it("does not submit when the confirmation differs from the new password", () => {
    render(<PasswordCard />);
    fill("old-secret", "new-secret-1", "new-secret-2");
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    expect(changeOwnPassword).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe("Passwords do not match");
  });

  it("keeps the button disabled until the confirmation is entered", () => {
    render(<PasswordCard />);
    fill("old-secret", "new-secret-1", "");

    expect((screen.getByRole("button", { name: "Change password" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("submits a matching pair and clears all three fields", async () => {
    const onChanged = vi.fn();
    render(<PasswordCard onChanged={onChanged} />);
    fill("old-secret", "new-secret-1", "new-secret-1");
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(changeOwnPassword).toHaveBeenCalledWith("old-secret", "new-secret-1");
    expect((screen.getByLabelText("Confirm new password") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("New password (at least 8 characters)") as HTMLInputElement).value).toBe("");
  });
});

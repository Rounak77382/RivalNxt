/**
 * F5: heavy modals must be code-split and must not load until first opened.
 *
 * ModModal (112 KB of source) was imported statically by five components;
 * GetStartedDialog, SettingsDialog, BackupModal, AssignModIdModal and
 * CrashDetectorModal were imported statically by App.tsx AND rendered
 * unconditionally. All of it sat in the initial bundle.
 */
import { render, screen } from "@testing-library/react";
import { Suspense, lazy, useState } from "react";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";
import { useHasBeenTrue } from "../lazyMount";

// ---------------------------------------------------------------------------
// useHasBeenTrue: the latch that keeps the chunk out of the initial load
// ---------------------------------------------------------------------------
function Latched({ flag }: { flag: boolean }) {
  const latched = useHasBeenTrue(flag);
  return <span data-testid="latched">{String(latched)}</span>;
}

describe("useHasBeenTrue", () => {
  it("starts false when the flag starts false", () => {
    render(<Latched flag={false} />);
    expect(screen.getByTestId("latched")).toHaveTextContent("false");
  });

  it("is true immediately on the first true render (no wasted frame)", () => {
    render(<Latched flag />);
    expect(screen.getByTestId("latched")).toHaveTextContent("true");
  });

  it("STAYS true after the flag goes back to false", () => {
    // This is the whole point: gating purely on `open` would unmount the modal on
    // close, discarding its state and any prop-driven effects.
    const { rerender } = render(<Latched flag={false} />);
    expect(screen.getByTestId("latched")).toHaveTextContent("false");

    rerender(<Latched flag />);
    expect(screen.getByTestId("latched")).toHaveTextContent("true");

    rerender(<Latched flag={false} />);
    expect(screen.getByTestId("latched")).toHaveTextContent("true");
  });

  it("survives repeated open/close cycles", () => {
    const { rerender } = render(<Latched flag={false} />);
    for (let i = 0; i < 4; i++) {
      rerender(<Latched flag />);
      rerender(<Latched flag={false} />);
    }
    expect(screen.getByTestId("latched")).toHaveTextContent("true");
  });
});

// ---------------------------------------------------------------------------
// The lazy + latch pattern: the import must not run until first open
// ---------------------------------------------------------------------------
describe("lazy modal gating", () => {
  it("does not evaluate the dynamic import until the gate opens", async () => {
    const importer = vi.fn(async () => ({
      default: () => <div data-testid="heavy">heavy modal</div>,
    }));
    const Heavy = lazy(importer);

    function Host() {
      const [open, setOpen] = useState(false);
      const everOpened = useHasBeenTrue(open);
      return (
        <>
          <button onClick={() => setOpen(true)}>open</button>
          {everOpened && (
            <Suspense fallback={<div data-testid="pending">…</div>}>
              <Heavy />
            </Suspense>
          )}
        </>
      );
    }

    render(<Host />);
    // Closed: the module factory must not have been called at all.
    expect(importer).not.toHaveBeenCalled();
    expect(screen.queryByTestId("heavy")).toBeNull();

    await act(async () => {
      screen.getByText("open").click();
    });

    expect(importer).toHaveBeenCalledTimes(1);
    expect(await screen.findByTestId("heavy")).toBeInTheDocument();
  });

  it("keeps the modal mounted after it closes again", async () => {
    const importer = vi.fn(async () => ({
      default: ({ open }: { open: boolean }) => (
        <div data-testid="heavy">{open ? "open" : "closed"}</div>
      ),
    }));
    const Heavy = lazy(importer);

    function Host() {
      const [open, setOpen] = useState(false);
      const everOpened = useHasBeenTrue(open);
      return (
        <>
          <button onClick={() => setOpen((v) => !v)}>toggle</button>
          {everOpened && (
            <Suspense fallback={null}>
              <Heavy open={open} />
            </Suspense>
          )}
        </>
      );
    }

    render(<Host />);
    await act(async () => {
      screen.getByText("toggle").click();
    });
    expect(await screen.findByTestId("heavy")).toHaveTextContent("open");

    await act(async () => {
      screen.getByText("toggle").click();
    });
    // Still mounted (now reporting closed), and the import ran only once.
    expect(screen.getByTestId("heavy")).toHaveTextContent("closed");
    expect(importer).toHaveBeenCalledTimes(1);
  });

  it("imports only once across many open/close cycles", async () => {
    const importer = vi.fn(async () => ({
      default: () => <div data-testid="heavy">h</div>,
    }));
    const Heavy = lazy(importer);

    function Host() {
      const [open, setOpen] = useState(false);
      const everOpened = useHasBeenTrue(open);
      return (
        <>
          <button onClick={() => setOpen((v) => !v)}>toggle</button>
          {everOpened && (
            <Suspense fallback={null}>
              <Heavy />
            </Suspense>
          )}
        </>
      );
    }

    render(<Host />);
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        screen.getByText("toggle").click();
      });
    }
    expect(importer).toHaveBeenCalledTimes(1);
  });
});

import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";

const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL || "admin@atelier.example";
const ADMIN_PASSWORD =
  process.env.E2E_ADMIN_PASSWORD || process.env.DEMO_ADMIN_PASSWORD || "AtelierAdmin!2026";
const STAFF_EMAIL = process.env.E2E_STAFF_EMAIL || "staff@atelier.example";
const STAFF_PASSWORD =
  process.env.E2E_STAFF_PASSWORD || process.env.DEMO_STAFF_PASSWORD || "AtelierStaff!2026";
const CUSTOMER_EMAIL = process.env.E2E_CUSTOMER_EMAIL || "customer@atelier.example";
const CUSTOMER_PASSWORD =
  process.env.E2E_CUSTOMER_PASSWORD || process.env.DEMO_CUSTOMER_PASSWORD || "AtelierCustomer!2026";

const SAFE_LOCAL_STORAGE_KEYS = new Set(["atelier-theme"]);

interface ShippingInfo {
  fullName: string;
  line1: string;
  city: string;
  state: string;
  postalCode: string;
  phone: string;
}

async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.locator("#login-email")).toHaveCount(1);
  await expect(page.locator("#login-password")).toHaveCount(1);
  await page.locator("#login-email").fill(email);
  await page.locator("#login-password").fill(password);
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  await expect(page).toHaveURL(/\/account/);
  await expect(page.getByText(email)).toBeVisible();
}

async function fillShippingAddress(page: Page, info: ShippingInfo): Promise<void> {
  await page.locator("#shipping-full_name").fill(info.fullName);
  await page.locator("#shipping-line1").fill(info.line1);
  await page.locator("#shipping-city").fill(info.city);
  await page.locator("#shipping-state").fill(info.state);
  await page.locator("#shipping-postal_code").fill(info.postalCode);
  await page.locator("#shipping-phone").fill(info.phone);
  await expect(page.locator("#billing-same-as-shipping")).toBeChecked();
}

async function goFromPdpToCheckoutViaCart(page: Page, expectedSku: string): Promise<void> {
  await expect(page.getByText(`SKU: ${expectedSku}`)).toBeVisible();
  await page.getByRole("button", { name: "Add to Cart" }).click();

  const viewFullCartLink = page.getByRole("link", { name: "View Full Cart" });
  await expect(viewFullCartLink).toBeVisible();
  await viewFullCartLink.click();

  await expect(page).toHaveURL(/\/cart$/);
  await expect(page.getByText(`SKU: ${expectedSku}`)).toBeVisible();

  await page.getByRole("link", { name: "Proceed to Checkout" }).click();
  await expect(page).toHaveURL(/\/checkout$/);
}

async function submitCheckoutAndCaptureOrderNumber(page: Page): Promise<string> {
  await page.getByRole("button", { name: /Place Order/i }).click();
  await expect(page).toHaveURL(/\/orders\/ORD-[A-Z0-9-]+$/);
  const match = page.url().match(/\/orders\/(ORD-[A-Z0-9-]+)$/);
  if (!match) {
    throw new Error(`Unexpected order URL after checkout: ${page.url()}`);
  }
  return match[1];
}

async function findInventoryRow(page: Page, sku: string) {
  const searchInput = page.locator("#inventory-search");
  await searchInput.fill(sku);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  const row = page.locator("tbody tr", { hasText: sku }).first();
  await expect(row).toBeVisible();
  return row;
}

async function readInventoryRowCounts(
  page: Page,
  sku: string
): Promise<{ onHand: number; reserved: number; available: number }> {
  const row = await findInventoryRow(page, sku);
  const cells = row.locator("td");
  const onHandText = (await cells.nth(2).innerText()).trim();
  const reservedText = (await cells.nth(3).innerText()).trim();
  const onHand = Number.parseInt(onHandText, 10);
  const reserved = Number.parseInt(reservedText, 10);
  if (Number.isNaN(onHand) || Number.isNaN(reserved)) {
    throw new Error(`Invalid stock counts for ${sku}: onHand="${onHandText}", reserved="${reservedText}"`);
  }
  return { onHand, reserved, available: onHand - reserved };
}

async function openContext(browser: Browser): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext();
  const page = await context.newPage();
  return { context, page };
}

test.describe.serial("QA-E2E-001: Live Storefront, Checkout, Payments, RBAC & Admin", () => {
  let customerOrderNumber = "";

  test("1. Authenticated customer purchase, sandbox payment & order history", async ({ browser }) => {
    const { context, page } = await openContext(browser);
    try {
      await signIn(page, CUSTOMER_EMAIL, CUSTOMER_PASSWORD);

      // Verify customer account cannot access Admin Console
      await page.goto("/admin");
      await expect(page.getByText("403 Access Denied")).toBeVisible();

      // Browse catalog -> PDP (Atelier Linen Overshirt, Sand / Medium, on_hand=24)
      await page.goto("/products");
      await page.getByRole("link", { name: /Atelier Linen Overshirt/i }).first().click();
      await expect(page).toHaveURL(/\/products\/linen-overshirt$/);

      await page
        .getByRole("radiogroup", { name: "Size" })
        .getByRole("radio", { name: /^M(?:\s|$)/ })
        .click();

      await goFromPdpToCheckoutViaCart(page, "ATL-LINEN-SND-M");

      await expect(page.locator("#checkout-email")).toHaveValue(CUSTOMER_EMAIL);
      await fillShippingAddress(page, {
        fullName: "Atelier Customer",
        line1: "100 Market Street",
        city: "San Francisco",
        state: "CA",
        postalCode: "94105",
        phone: "+14155550101",
      });

      customerOrderNumber = await submitCheckoutAndCaptureOrderNumber(page);
      await expect(page.getByRole("heading", { name: customerOrderNumber })).toBeVisible();

      // Ensure zero payment card inputs are ever rendered (sandbox SimPay only)
      const forbiddenCardSelectors = [
        'input[name*="card" i]',
        'input[id*="card" i]',
        'input[autocomplete*="cc-" i]',
        'input[placeholder*="card" i]',
        'input[name*="cvv" i]',
        'input[name*="cvc" i]',
        'input[name*="pan" i]',
        'input[name*="expir" i]',
      ];
      for (const selector of forbiddenCardSelectors) {
        await expect(page.locator(selector)).toHaveCount(0);
      }

      // Simulate sandbox payment success -> webhook confirms order
      await page.getByRole("button", { name: "Simulate Payment Success" }).click();
      await expect(page.getByText(/Payment confirmed!/i)).toBeVisible();
      await expect(page.getByText("Confirmed", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("Paid", { exact: true }).first()).toBeVisible();

      // Verify order appears in customer order history
      await page.goto("/orders");
      await expect(page.getByRole("heading", { name: "My Orders" })).toBeVisible();
      await expect(page.getByRole("link", { name: customerOrderNumber })).toBeVisible();
    } finally {
      await context.close();
    }
  });

  test("2. Guest checkout, HttpOnly cookie reload access, zero storage tokens & self-cancel", async ({
    browser,
  }) => {
    const { context: guestContext, page: guestPage } = await openContext(browser);
    try {
      await guestPage.goto("/products");
      await guestPage.getByRole("link", { name: /Waxed Canvas Studio Tote/i }).first().click();
      await expect(guestPage).toHaveURL(/\/products\/canvas-tote$/);

      await goFromPdpToCheckoutViaCart(guestPage, "ATL-TOTE-STN-OS");

      await guestPage.locator("#checkout-email").fill("guest.e2e@example.com");
      await fillShippingAddress(guestPage, {
        fullName: "Guest Buyer",
        line1: "250 Mercer Street",
        city: "New York",
        state: "NY",
        postalCode: "10012",
        phone: "+12125550199",
      });

      const guestOrderNumber = await submitCheckoutAndCaptureOrderNumber(guestPage);
      await expect(guestPage.getByRole("heading", { name: guestOrderNumber })).toBeVisible();
      await expect(guestPage.getByText("Pending Payment", { exact: true }).first()).toBeVisible();

      // Reload wipes in-memory token; access must succeed via HttpOnly ecom_guest_orders cookie
      await guestPage.reload();
      await expect(guestPage.getByRole("heading", { name: guestOrderNumber })).toBeVisible();
      await expect(guestPage.getByText("ATL-TOTE-STN-OS")).toBeVisible();
      await expect(guestPage.getByText("Order Access Required")).toHaveCount(0);

      // Verify HttpOnly guest order cookie exists and is hidden from JS/storage
      const contextCookies = await guestContext.cookies();
      const guestCookie = contextCookies.find((cookie) => cookie.name === "ecom_guest_orders");
      expect(guestCookie).toBeDefined();
      expect(guestCookie?.httpOnly).toBe(true);

      const storageAudit = await guestPage.evaluate(() => ({
        docCookie: document.cookie,
        localKeys: Object.keys(window.localStorage),
        sessionKeys: Object.keys(window.sessionStorage),
      }));
      expect(storageAudit.docCookie).not.toContain("ecom_guest_orders");
      expect(storageAudit.docCookie).not.toContain("ecom_session");
      expect(storageAudit.sessionKeys).toEqual([]);
      for (const key of storageAudit.localKeys) {
        expect(SAFE_LOCAL_STORAGE_KEYS.has(key)).toBe(true);
      }

      // Self-cancel unpaid guest order and confirm cancellation
      await guestPage.getByRole("button", { name: "Cancel Order" }).click();
      const cancelDialog = guestPage.getByRole("dialog");
      await expect(cancelDialog).toBeVisible();
      await cancelDialog.locator("#cancel-order-reason").fill("E2E guest cancellation");
      await cancelDialog.getByRole("button", { name: "Confirm Cancellation" }).click();

      await expect(cancelDialog).toHaveCount(0);
      await expect(guestPage.getByText("Cancelled", { exact: true }).first()).toBeVisible();
      await expect(guestPage.getByText(/Reserved inventory has been released\./i)).toBeVisible();
    } finally {
      await guestContext.close();
    }
  });

  test("3. Staff RBAC & fulfillment, Admin dashboard analytics, refund+restock & audit diff", async ({
    browser,
  }) => {
    expect(customerOrderNumber).toMatch(/^ORD-[A-Z0-9-]+$/);

    // Part A: Staff operator RBAC restrictions and order fulfillment
    const { context: staffContext, page: staffPage } = await openContext(browser);
    try {
      await signIn(staffPage, STAFF_EMAIL, STAFF_PASSWORD);

      await staffPage.goto("/admin/users");
      await expect(staffPage.getByText("403 Restricted to Administrators")).toBeVisible();

      await staffPage.goto("/admin/audit-logs");
      await expect(staffPage.getByText("403 Restricted to Administrators")).toBeVisible();

      await staffPage.goto("/admin/orders");
      await staffPage.locator("#orders-search").fill(customerOrderNumber);
      await staffPage.getByRole("button", { name: "Apply Filters" }).click();
      await staffPage.getByRole("link", { name: customerOrderNumber }).click();

      await expect(staffPage).toHaveURL(new RegExp(`/admin/orders/${customerOrderNumber}$`));
      await expect(staffPage.getByRole("button", { name: /Issue Refund/i })).toHaveCount(0);

      // Start Processing
      await staffPage.getByRole("button", { name: "Start Processing" }).click();
      let fulfillDialog = staffPage.getByRole("dialog");
      await expect(fulfillDialog).toBeVisible();
      await fulfillDialog.getByRole("button", { name: "Start Processing" }).click();
      await expect(fulfillDialog).toHaveCount(0);
      await expect(staffPage.getByText("Processing", { exact: true }).first()).toBeVisible();

      // Mark Shipped
      await staffPage.getByRole("button", { name: "Mark Shipped" }).click();
      fulfillDialog = staffPage.getByRole("dialog");
      await expect(fulfillDialog).toBeVisible();
      await fulfillDialog.locator("#ship-carrier").fill("UPS");
      await fulfillDialog.locator("#ship-tracking").fill("1Z999AA10123456784");
      await fulfillDialog.getByRole("button", { name: "Mark Shipped" }).click();
      await expect(fulfillDialog).toHaveCount(0);
      await expect(staffPage.getByText("Shipped", { exact: true }).first()).toBeVisible();

      // Mark Completed
      await staffPage.getByRole("button", { name: "Mark Completed" }).click();
      fulfillDialog = staffPage.getByRole("dialog");
      await expect(fulfillDialog).toBeVisible();
      await fulfillDialog.getByRole("button", { name: "Mark Completed" }).click();
      await expect(fulfillDialog).toHaveCount(0);
      await expect(staffPage.getByText("Completed", { exact: true }).first()).toBeVisible();
      await expect(staffPage.getByText("Order fulfilled and delivered.")).toBeVisible();
    } finally {
      await staffContext.close();
    }

    // Part B: Admin dashboard analytics, refund with restock, inventory verification & audit diff
    const { context: adminContext, page: adminPage } = await openContext(browser);
    try {
      await signIn(adminPage, ADMIN_EMAIL, ADMIN_PASSWORD);
      await adminPage.goto("/admin");
      await expect(
        adminPage.getByRole("heading", { name: "Sales & Operations Dashboard" })
      ).toBeVisible();

      const netRevenueCard = adminPage
        .locator(".rounded-lg.border")
        .filter({ has: adminPage.getByRole("heading", { name: "Net Revenue", exact: true }) });
      const paidOrdersCard = adminPage
        .locator(".rounded-lg.border")
        .filter({ has: adminPage.getByRole("heading", { name: "Paid Orders", exact: true }) });

      const netRevenueValue = netRevenueCard.locator(".text-2xl");
      const paidOrdersValue = paidOrdersCard.locator(".text-2xl");
      await expect(netRevenueValue).toHaveText(/^\$[1-9][\d,]*\.\d{2}$/);
      await expect(paidOrdersValue).toHaveText(/^[1-9]\d*$/);

      // Verify guest cancellation released ATL-TOTE-STN-OS stock and note ATL-LINEN-SND-M before refund
      await adminPage.goto("/admin/inventory");
      const toteCounts = await readInventoryRowCounts(adminPage, "ATL-TOTE-STN-OS");
      expect(toteCounts.onHand).toBe(32);
      expect(toteCounts.reserved).toBe(0);

      const linenMediumBefore = await readInventoryRowCounts(adminPage, "ATL-LINEN-SND-M");
      expect(linenMediumBefore.onHand).toBe(23);

      // Issue full refund with restock checked
      await adminPage.goto(`/admin/orders/${encodeURIComponent(customerOrderNumber)}`);
      const refundBtn = adminPage.getByRole("button", { name: "Issue Refund" });
      await expect(refundBtn).toBeVisible();
      await refundBtn.click();

      const refundDialog = adminPage.getByRole("dialog");
      await expect(refundDialog).toBeVisible();
      const restockCheckbox = refundDialog.locator("#refund-restock");
      if ((await restockCheckbox.getAttribute("aria-checked")) !== "true") {
        await restockCheckbox.click();
      }
      await expect(restockCheckbox).toHaveAttribute("aria-checked", "true");
      await refundDialog.locator("#refund-reason").fill("E2E customer return and restock");
      await refundDialog.getByRole("button", { name: "Confirm Full Refund" }).click();

      await expect(refundDialog).toHaveCount(0);
      await expect(adminPage.getByText("Refunded", { exact: true }).first()).toBeVisible();

      // Confirm on_hand increased back by purchased quantity (+1 -> 24)
      await adminPage.goto("/admin/inventory");
      const linenMediumAfter = await readInventoryRowCounts(adminPage, "ATL-LINEN-SND-M");
      expect(linenMediumAfter.onHand).toBe(linenMediumBefore.onHand + 1);

      // Verify ORDER_REFUNDED audit log entry and inspect Before/After JSON Diff drawer
      await adminPage.goto("/admin/audit-logs");
      await adminPage.locator("#audit-action").fill("ORDER_REFUNDED");
      await adminPage.getByRole("button", { name: "Apply Filters" }).click();

      const auditRow = adminPage
        .locator("tbody tr", { hasText: "ORDER_REFUNDED" })
        .filter({ hasText: customerOrderNumber })
        .first();
      await expect(auditRow).toBeVisible();
      await auditRow.getByRole("button", { name: /Inspect/i }).click();

      const diffDrawer = adminPage.getByRole("dialog");
      await expect(diffDrawer).toBeVisible();
      await expect(diffDrawer.getByRole("heading", { name: "Before State" })).toBeVisible();
      await expect(diffDrawer.getByRole("heading", { name: "After State" })).toBeVisible();
      await expect(diffDrawer.getByText(/"COMPLETED"/).first()).toBeVisible();
      await expect(diffDrawer.getByText(/"REFUNDED"/).first()).toBeVisible();
      await expect(diffDrawer.getByText(/"RETURNED"/).first()).toBeVisible();
    } finally {
      await adminContext.close();
    }
  });

  test("4. Cross-context concurrent oversell guard prevents second order on last unit", async ({
    browser,
  }) => {
    // Adjust ATL-LINEN-SND-L cleanly so available_quantity === 1
    const { context: adminContext, page: adminPage } = await openContext(browser);
    try {
      await signIn(adminPage, ADMIN_EMAIL, ADMIN_PASSWORD);
      await adminPage.goto("/admin/inventory");
      const initialCounts = await readInventoryRowCounts(adminPage, "ATL-LINEN-SND-L");
      const deltaNeeded = 1 - initialCounts.available;

      if (deltaNeeded !== 0) {
        const row = await findInventoryRow(adminPage, "ATL-LINEN-SND-L");
        await row.getByRole("button", { name: /Adjust Stock/i }).click();
        const adjustDialog = adminPage.getByRole("dialog");
        await expect(adjustDialog).toBeVisible();
        await adjustDialog.locator("#adjust-delta").fill(String(deltaNeeded));
        await adjustDialog
          .locator("#adjust-note")
          .fill("E2E oversell guard calibration to 1 available unit");
        await adjustDialog.getByRole("button", { name: "Apply Adjustment" }).click();
        await expect(adjustDialog).toHaveCount(0);
        await expect(
          adminPage.locator("tbody tr", { hasText: "ATL-LINEN-SND-L" }).first()
        ).toContainText("Low stock — only 1 left");
      }

      const calibrated = await readInventoryRowCounts(adminPage, "ATL-LINEN-SND-L");
      expect(calibrated.available).toBe(1);
    } finally {
      await adminContext.close();
    }

    // Both Context A and Context B put the last available unit in their bags and reach /checkout
    const { context: contextA, page: pageA } = await openContext(browser);
    const { context: contextB, page: pageB } = await openContext(browser);
    try {
      for (const [ctxPage, email, name] of [
        [pageA, "guest.race.a@example.com", "Race Buyer A"],
        [pageB, "guest.race.b@example.com", "Race Buyer B"],
      ] as const) {
        await ctxPage.goto("/products/linen-overshirt");
        await ctxPage
          .getByRole("radiogroup", { name: "Size" })
          .getByRole("radio", { name: /^L(?:\s|$)/ })
          .click();
        await expect(ctxPage.getByText("SKU: ATL-LINEN-SND-L")).toBeVisible();
        await ctxPage.getByRole("button", { name: "Add to Cart" }).click();
        const checkoutLink = ctxPage.getByRole("link", { name: "Proceed to Checkout" });
        await expect(checkoutLink).toBeVisible();
        await checkoutLink.click();
        await expect(ctxPage).toHaveURL(/\/checkout$/);

        await ctxPage.locator("#checkout-email").fill(email);
        await fillShippingAddress(ctxPage, {
          fullName: name,
          line1: "500 Howard Street",
          city: "San Francisco",
          state: "CA",
          postalCode: "94105",
          phone: "+14155550177",
        });
      }

      // Context A places order -> succeeds and reserves the sole remaining unit
      const orderA = await submitCheckoutAndCaptureOrderNumber(pageA);
      await expect(pageA.getByRole("heading", { name: orderA })).toBeVisible();
      await expect(pageA.getByText("Pending Payment", { exact: true }).first()).toBeVisible();

      // Context B attempts to place order -> backend 409 INSUFFICIENT_STOCK blocks oversell cleanly
      await pageB.getByRole("button", { name: /Place Order/i }).click();
      await expect(pageB).toHaveURL(/\/checkout$/);
      await expect(
        pageB.getByRole("alert").filter({ hasText: /insufficient|available|stock/i }).first()
      ).toBeVisible();
      await expect(pageB.getByRole("button", { name: /Place Order/i })).toBeDisabled();

      // PDP reflects Out of stock for size L while Context A holds the active reservation
      await pageB.goto("/products/linen-overshirt");
      await pageB
        .getByRole("radiogroup", { name: "Size" })
        .getByRole("radio", { name: /^L(?:\s|$)/ })
        .click();
      await expect(pageB.getByText("Out of stock", { exact: true })).toBeVisible();
      await expect(pageB.getByRole("button", { name: "Out of Stock" })).toBeDisabled();
    } finally {
      await contextA.close();
      await contextB.close();
    }
  });
});

const SEEDED_PRODUCT_SLUGS = [
  "linen-overshirt",
  "merino-crewneck",
  "leather-weekender",
  "canvas-tote",
  "stoneware-mug-set",
  "walnut-serving-board",
  "ceramic-table-lamp",
  "wool-throw-blanket",
] as const;

async function assertAllMainImagesLoaded(page: Page, contextLabel: string): Promise<number> {
  const mainImages = page.locator("main img");
  await expect
    .poll(async () => await mainImages.count(), {
      message: `Expected at least 1 rendered <img> inside <main> (${contextLabel})`,
    })
    .toBeGreaterThan(0);

  const count = await mainImages.count();
  await expect
    .poll(
      async () =>
        mainImages.evaluateAll((imgs) =>
          imgs.every((img) => {
            const el = img as HTMLImageElement;
            return el.complete && el.naturalWidth > 0;
          })
        ),
      { message: `Expected all ${count} main <img> elements to load cleanly (${contextLabel})` }
    )
    .toBe(true);
  return count;
}

async function assertElementUnobstructed(
  locator: ReturnType<Page["locator"]>,
  label: string
): Promise<void> {
  await locator.scrollIntoViewIfNeeded();
  await expect(locator, `Expected ${label} to be visible`).toBeVisible();
  const hitOk = await locator.evaluate((el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      return false;
    }
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const topEl = document.elementFromPoint(cx, cy);
    return topEl !== null && (el === topEl || el.contains(topEl) || topEl.contains(el));
  });
  expect(hitOk, `Expected ${label} to be unobstructed by fixed overlays`).toBe(true);
}

test.describe("REM-P2-M09 & FE-REDESIGN-001: Storefront SSR, Metadata, Theme, Assets & Mobile Nav", () => {
  test("5. Raw HTTP SSR HTML, dynamic PDP <title>, theme toggle/persistence, hero & all 8 PNGs, HTTP 404 & mobile nav", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    for (const route of ["/", "/products", "/products/linen-overshirt"] as const) {
      const response = await page.request.get(route);
      expect(response.status(), `Expected HTTP 200 for ${route}`).toBe(200);
      const html = await response.text();
      expect(html, `Expected SSR product content in ${route}`).toContain("Atelier Linen Overshirt");
      if (route === "/products/linen-overshirt") {
        expect(html).toMatch(/<title>Atelier Linen Overshirt \| Vellora Store<\/title>/i);
      }
    }

    const cartResponse = await page.request.get("/cart");
    expect(cartResponse.status()).toBe(200);
    expect(cartResponse.headers()["cache-control"]).toContain("no-store");

    const notFoundResponse = await page.request.get("/products/nonexistent-product-slug-404");
    expect(notFoundResponse.status()).toBe(404);

    // Home (/) Desktop 1440x900: default dark mode, hero showcase PNG, all main imgs loaded, and Light/Dark pill persistence
    await page.goto("/");
    await expect(page.locator("html")).toHaveClass(/\bdark\b/);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    const heroImg = page.locator('main img[src$="/assets/products/hero-showcase.png"]');
    await expect(heroImg, "Expected rendered hero showcase image on /").toBeVisible();
    await expect
      .poll(
        async () =>
          heroImg.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0),
        { message: "Expected naturalWidth > 0 for /assets/products/hero-showcase.png" }
      )
      .toBe(true);
    await assertAllMainImagesLoaded(page, "desktop /");

    const heroAssetRes = await page.request.get("/assets/products/hero-showcase.png");
    expect(heroAssetRes.status()).toBe(200);
    expect(heroAssetRes.headers()["content-type"]).toContain("image/png");

    const themeGroup = page.getByRole("group", { name: "Toggle color theme" });
    await expect(themeGroup).toBeVisible();
    await themeGroup.getByRole("button", { name: "Light theme" }).click();
    await expect(page.locator("html")).not.toHaveClass(/\bdark\b/);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    await page.reload();
    await expect(page.locator("html")).not.toHaveClass(/\bdark\b/);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    const themeStorageAudit = await page.evaluate(() => ({
      localKeys: Object.keys(window.localStorage),
      themeValue: window.localStorage.getItem("atelier-theme"),
    }));
    expect(themeStorageAudit.themeValue).toBe("light");
    for (const key of themeStorageAudit.localKeys) {
      expect(SAFE_LOCAL_STORAGE_KEYS.has(key)).toBe(true);
    }

    await page
      .getByRole("group", { name: "Toggle color theme" })
      .getByRole("button", { name: "Dark theme" })
      .click();
    await expect(page.locator("html")).toHaveClass(/\bdark\b/);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    // Catalog (/products) Desktop 1440x900: heading hierarchy + all 8 seeded PNGs loaded
    await page.goto("/products");
    await expect(page.getByRole("heading", { level: 1, name: "Collection" })).toBeVisible();
    expect(await page.locator("main h2").count()).toBeGreaterThanOrEqual(8);
    await expect(page.locator("main h3")).toHaveCount(0);

    for (const slug of SEEDED_PRODUCT_SLUGS) {
      const assetPath = `/assets/products/${slug}.png`;
      const img = page.locator(`main img[src$="${assetPath}"]`);
      await img.scrollIntoViewIfNeeded();
      await expect(img, `Expected rendered <img> for ${assetPath}`).toBeVisible();
      await expect
        .poll(
          async () =>
            img.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0),
          { message: `Expected naturalWidth > 0 for ${assetPath}` }
        )
        .toBe(true);

      const assetResponse = await page.request.get(assetPath);
      expect(assetResponse.status(), `Expected HTTP 200 for ${assetPath}`).toBe(200);
      expect(assetResponse.headers()["content-type"]).toContain("image/png");
    }
    await assertAllMainImagesLoaded(page, "desktop /products");

    const notFoundPageResponse = await page.goto("/products/nonexistent-product-slug-404");
    expect(notFoundPageResponse?.status()).toBe(404);
    await expect(page.locator("main")).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();

    // Mobile viewport (390x844): images, MobileBottomNav visibility, scroll clearance & hidden routes
    await page.setViewportSize({ width: 390, height: 844 });
    const mobileNavSelector = 'nav[aria-label="Mobile bottom navigation"]';

    // 1. Mobile /
    await page.goto("/");
    const mobileNav = page.locator(mobileNavSelector);
    await expect(mobileNav).toBeVisible();
    for (const label of ["Home", "Catalog", "Cart", "Account"] as const) {
      await expect(mobileNav.getByRole("link", { name: label })).toBeVisible();
    }
    await expect(page.locator('main img[src$="/assets/products/hero-showcase.png"]')).toBeVisible();
    await assertAllMainImagesLoaded(page, "mobile /");

    const categoriesRail = page.locator("#home-categories-rail");
    await expect(categoriesRail).toBeVisible();
    await expect(categoriesRail).toHaveAttribute("role", "region");
    await expect(categoriesRail).toHaveAttribute("aria-label", "Popular categories");
    const railMetricsBefore = await categoriesRail.evaluate((el) => ({
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
      scrollLeft: el.scrollLeft,
    }));
    expect(railMetricsBefore.scrollWidth).toBeGreaterThan(railMetricsBefore.clientWidth);
    expect(railMetricsBefore.scrollLeft).toBe(0);

    const scrollLeftBtn = page.getByRole("button", { name: "Scroll categories left" });
    const scrollRightBtn = page.getByRole("button", { name: "Scroll categories right" });
    await expect(scrollLeftBtn).toBeVisible();
    await expect(scrollLeftBtn).toBeDisabled();
    await expect(scrollRightBtn).toBeVisible();
    await expect(scrollRightBtn).toBeEnabled();

    await scrollRightBtn.click();
    await expect
      .poll(async () => await categoriesRail.evaluate((el) => el.scrollLeft), {
        message: "Expected #home-categories-rail scrollLeft > 0 after clicking Scroll categories right",
      })
      .toBeGreaterThan(0);
    await expect(scrollLeftBtn).toBeEnabled();

    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const homeMobileLayout = await page.evaluate((navSel) => {
      const footer = document.querySelector("footer");
      const nav = document.querySelector(navSel);
      return {
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
        scrollY: window.scrollY,
        footerBottom: footer?.getBoundingClientRect().bottom ?? 0,
        navTop: nav?.getBoundingClientRect().top ?? 0,
      };
    }, mobileNavSelector);
    expect(homeMobileLayout.scrollWidth).toBeLessThanOrEqual(homeMobileLayout.innerWidth);
    expect(homeMobileLayout.scrollY).toBeGreaterThan(0);
    expect(homeMobileLayout.footerBottom).toBeLessThanOrEqual(homeMobileLayout.navTop + 1);

    // 2. Mobile /products (all 8 PNGs + MobileBottomNav visible + zero horizontal overflow + scroll clearance)
    await page.goto("/products");
    await expect(mobileNav).toBeVisible();
    for (const slug of SEEDED_PRODUCT_SLUGS) {
      const assetPath = `/assets/products/${slug}.png`;
      await expect(page.locator(`main img[src$="${assetPath}"]`)).toBeVisible();
    }
    await assertAllMainImagesLoaded(page, "mobile /products");

    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const catalogMobileLayout = await page.evaluate((navSel) => {
      const footer = document.querySelector("footer");
      const nav = document.querySelector(navSel);
      return {
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
        scrollY: window.scrollY,
        footerBottom: footer?.getBoundingClientRect().bottom ?? 0,
        navTop: nav?.getBoundingClientRect().top ?? 0,
      };
    }, mobileNavSelector);
    expect(catalogMobileLayout.scrollWidth).toBeLessThanOrEqual(catalogMobileLayout.innerWidth);
    expect(catalogMobileLayout.scrollY).toBeGreaterThan(0);
    expect(catalogMobileLayout.footerBottom).toBeLessThanOrEqual(catalogMobileLayout.navTop + 1);

    // 3. Mobile /products/linen-overshirt (PDP): MobileBottomNav absent, controls unobstructed, size selection & Add to Cart
    await page.goto("/products/linen-overshirt");
    await expect(page.locator(mobileNavSelector)).toHaveCount(0);

    const pdpSizeGroup = page.getByRole("radiogroup", { name: "Size" });
    const pdpSizeM = pdpSizeGroup.getByRole("radio", { name: /^M(?:\s|$)/ });
    const pdpSizeL = pdpSizeGroup.getByRole("radio", { name: /^L(?:\s|$)/ });
    const pdpDecreaseQtyBtn = page.getByRole("button", { name: "Decrease quantity" });
    const pdpIncreaseQtyBtn = page.getByRole("button", { name: "Increase quantity" });
    const pdpAddToCartBtn = page.getByRole("button", { name: "Add to Cart" });

    await assertElementUnobstructed(pdpSizeM, "PDP Size M radio");
    await assertElementUnobstructed(pdpSizeL, "PDP Size L radio");
    await assertElementUnobstructed(pdpDecreaseQtyBtn, "PDP Decrease quantity button");
    await assertElementUnobstructed(pdpIncreaseQtyBtn, "PDP Increase quantity button");
    await assertElementUnobstructed(pdpAddToCartBtn, "PDP Add to Cart button");

    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await expect
      .poll(async () => await page.evaluate(() => window.scrollY))
      .toBeGreaterThan(0);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    ).toBe(true);

    // Verify Size "L" radio toggles cleanly (note: Test 4 reserved the last unit of ATL-LINEN-SND-L),
    // then select in-stock Size "M" and add to guest bag so /cart and /checkout remain deterministic.
    await pdpSizeL.scrollIntoViewIfNeeded();
    await pdpSizeL.click();
    await expect(pdpSizeL).toHaveAttribute("aria-checked", "true");
    await expect(page.getByText("SKU: ATL-LINEN-SND-L")).toBeVisible();

    await pdpSizeM.click();
    await expect(pdpSizeM).toHaveAttribute("aria-checked", "true");
    await expect(page.getByText("SKU: ATL-LINEN-SND-M")).toBeVisible();
    await pdpAddToCartBtn.click();
    await expect(page.getByRole("link", { name: "View Full Cart" })).toBeVisible();

    // 4. Mobile /cart: MobileBottomNav absent + CTA unobstructed
    await page.goto("/cart");
    await expect(page.locator(mobileNavSelector)).toHaveCount(0);
    await assertElementUnobstructed(
      page.getByRole("link", { name: "Proceed to Checkout" }),
      "Cart Proceed to Checkout CTA"
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    ).toBe(true);

    // 5. Mobile /checkout: MobileBottomNav absent + inputs & CTA unobstructed
    await page.goto("/checkout");
    await expect(page.locator(mobileNavSelector)).toHaveCount(0);
    await assertElementUnobstructed(page.locator("#checkout-email"), "Checkout email input");
    await assertElementUnobstructed(
      page.getByRole("button", { name: /Place Order/i }),
      "Checkout Place Order CTA"
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    ).toBe(true);

    // 6. Mobile /login: MobileBottomNav absent + #login-email & Sign In button unobstructed
    await page.goto("/login");
    await expect(page.locator(mobileNavSelector)).toHaveCount(0);
    await assertElementUnobstructed(page.locator("#login-email"), "Login email input");
    await assertElementUnobstructed(
      page.getByRole("button", { name: "Sign In", exact: true }),
      "Login Sign In button"
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    ).toBe(true);
  });
});

interface CspViolationRecord {
  violatedDirective: string;
  effectiveDirective: string;
  blockedURI: string;
  sourceFile: string;
}

type WindowWithCspLog = Window & {
  __cspViolations?: CspViolationRecord[];
};

function extractScriptSrcDirective(cspHeader: string): string {
  const directives = cspHeader.split(";").map((part) => part.trim());
  const scriptSrc = directives.find((d) => d.startsWith("script-src "));
  if (!scriptSrc) {
    throw new Error(`Missing script-src directive in CSP header: ${cspHeader}`);
  }
  return scriptSrc;
}

function extractNonceFromCsp(cspHeader: string): string {
  const scriptSrc = extractScriptSrcDirective(cspHeader);
  expect(scriptSrc).not.toContain("'unsafe-inline'");
  expect(scriptSrc).not.toContain("'unsafe-eval'");
  const match = scriptSrc.match(/^script-src\s+'self'\s+'nonce-([A-Za-z0-9+/=_-]+)'/);
  if (!match) {
    throw new Error(`Expected script-src 'self' 'nonce-...' in CSP header, got: ${scriptSrc}`);
  }
  return match[1];
}

test.describe("HARDEN-QA-01: Per-Request Nonce CSP, Security Headers & Zero Violations", () => {
  test("6. Strict nonce CSP headers, per-request nonce rotation, DOM script nonces & zero CSP violations across storefront + Recharts admin", async ({
    browser,
  }) => {
    const { context, page } = await openContext(browser);
    const consoleCspErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error" && /content security policy|csp/i.test(msg.text())) {
        consoleCspErrors.push(msg.text());
      }
    });

    await page.addInitScript(() => {
      const win = window as WindowWithCspLog;
      win.__cspViolations = [];
      document.addEventListener("securitypolicyviolation", (ev) => {
        win.__cspViolations?.push({
          violatedDirective: ev.violatedDirective,
          effectiveDirective: ev.effectiveDirective,
          blockedURI: ev.blockedURI,
          sourceFile: ev.sourceFile,
        });
      });
    });

    try {
      // 1. Consecutive requests to / must rotate CSP nonces & match inline script nonces
      const homeRes1 = await page.request.get("/");
      expect(homeRes1.status()).toBe(200);
      const homeCsp1 = homeRes1.headers()["content-security-policy"];
      expect(homeCsp1).toBeDefined();
      const nonce1 = extractNonceFromCsp(homeCsp1);
      const homeHtml1 = await homeRes1.text();
      expect(homeHtml1).toContain(`nonce="${nonce1}"`);

      const homeRes2 = await page.request.get("/");
      expect(homeRes2.status()).toBe(200);
      const homeCsp2 = homeRes2.headers()["content-security-policy"];
      expect(homeCsp2).toBeDefined();
      const nonce2 = extractNonceFromCsp(homeCsp2);
      expect(nonce2).not.toBe(nonce1);

      // 2. Verify security headers & strict CSP across key routes
      for (const route of ["/", "/products/linen-overshirt", "/login", "/admin"] as const) {
        const resp = await page.request.get(route);
        expect(resp.status(), `Unexpected status for ${route}`).toBe(200);
        const headers = resp.headers();
        const csp = headers["content-security-policy"];
        expect(csp, `Missing CSP header on ${route}`).toBeDefined();
        extractNonceFromCsp(csp);
        expect(headers["x-frame-options"]).toBe("DENY");
        expect(headers["x-content-type-options"]).toBe("nosniff");
        if (route === "/admin") {
          expect(headers["referrer-policy"]).toBe("no-referrer");
        }
        if (route === "/admin" || route === "/login") {
          expect(headers["cache-control"]).toContain("no-store");
        }
      }

      for (const noStoreRoute of ["/cart", "/checkout"] as const) {
        const resp = await page.request.get(noStoreRoute);
        expect(resp.status()).toBe(200);
        expect(resp.headers()["cache-control"]).toContain("no-store");
      }

      // 3. Rendered DOM inline <script> tags must carry nonces on storefront & login
      for (const route of ["/", "/products/linen-overshirt", "/login"] as const) {
        await page.goto(route);
        await page.waitForLoadState("networkidle");
        const inlineScripts = await page.evaluate(() => {
          const scripts = Array.from(document.querySelectorAll("script"));
          return scripts
            .filter((el) => !el.getAttribute("src"))
            .map((el) => ({
              hasNonceAttr: el.hasAttribute("nonce"),
              nonceProp: el.nonce || "",
              snippet: (el.textContent || "").slice(0, 60),
            }));
        });
        expect(inlineScripts.length, `Expected inline Next.js scripts on ${route}`).toBeGreaterThan(0);
        for (const info of inlineScripts) {
          expect(
            info.hasNonceAttr || info.nonceProp.length > 0,
            `Inline script missing nonce on ${route}: ${info.snippet}`
          ).toBe(true);
        }
      }

      // 4. Sign in as Admin and exercise Admin Analytics (Recharts SVG) with CSP violation listener
      await signIn(page, ADMIN_EMAIL, ADMIN_PASSWORD);
      await page.goto("/admin");
      await expect(
        page.getByRole("heading", { name: "Sales & Operations Dashboard" })
      ).toBeVisible();
      await expect
        .poll(async () => await page.locator("main svg.recharts-surface").count(), {
          message: "Expected Recharts SVG charts to render inside /admin",
        })
        .toBeGreaterThan(0);

      const violations = await page.evaluate(
        () => (window as WindowWithCspLog).__cspViolations ?? []
      );
      expect(violations, `Unexpected CSP violations: ${JSON.stringify(violations)}`).toEqual([]);
      expect(consoleCspErrors).toEqual([]);
    } finally {
      await context.close();
    }
  });
});

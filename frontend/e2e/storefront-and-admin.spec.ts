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

test.describe("REM-P2-M09: Storefront SSR, Dynamic Metadata, HTTP 404 & Asset Resolution", () => {
  test("5. Raw HTTP SSR HTML, dynamic PDP <title>, real HTTP 404 & all 8 PNG assets load cleanly", async ({
    page,
  }) => {
    for (const route of ["/", "/products", "/products/linen-overshirt"] as const) {
      const response = await page.request.get(route);
      expect(response.status(), `Expected HTTP 200 for ${route}`).toBe(200);
      const html = await response.text();
      expect(html, `Expected SSR product content in ${route}`).toContain("Atelier Linen Overshirt");
      if (route === "/products/linen-overshirt") {
        expect(html).toMatch(/<title>Atelier Linen Overshirt \| Atelier<\/title>/i);
      }
    }

    const cartResponse = await page.request.get("/cart");
    expect(cartResponse.status()).toBe(200);
    expect(cartResponse.headers()["cache-control"]).toContain("no-store");

    const notFoundResponse = await page.request.get("/products/nonexistent-product-slug-404");
    expect(notFoundResponse.status()).toBe(404);

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

    const notFoundPageResponse = await page.goto("/products/nonexistent-product-slug-404");
    expect(notFoundPageResponse?.status()).toBe(404);
    await expect(page.locator("main")).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
  });
});

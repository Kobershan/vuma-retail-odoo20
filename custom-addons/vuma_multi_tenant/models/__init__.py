from . import sale_order_line    # sale.order + sale.order.line, split logic
from . import product_template   # cross-company product/domain widening
from . import res_users
from . import account_move       # stock-source company on invoice lines
from . import vuma_payment_session  # cross-company cash collection
from . import vuma_credit_wizard    # credit invoices across both companies
from . import vuma_stock_trace      # stock <-> company traceability audit

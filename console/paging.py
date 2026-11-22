"""Page long staff lists (Lighthouse pass 3, Nov 22, 2026): the Diagnostics sessions and reports, the Emails log and
the Prospects list used to draw their first 200–300 rows and drop the rest; they now draw 50 at a time with Newer /
Older links that keep the filters (`console/_pager.html`). CSV exports stay complete."""
from django.core.paginator import Paginator

PER_PAGE = 50


def page(request, items, per_page=PER_PAGE):
    """A Django Page of `items` for ?page= (out of range → the last page, junk → the first), with `query` = the
    request's other parameters for the pager's links."""
    result = Paginator(items, per_page).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    result.query = params.urlencode()
    return result

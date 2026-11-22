from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

from attnd.permissions import PermissionMixin


class EmailOrUsernameBackend(PermissionMixin, ModelBackend):
    """Sign in with the account's email or its username, ignoring case. Permissions: attnd.permissions (event roles,
    the Staff role) plus Django's groups and per-user permissions.

    Accounts made with `createsuperuser` or the Django admin can have a username that isn't the
    email; this still lets them sign in with their email. An exact username match wins.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if not username or password is None:
            return None
        users = get_user_model()._default_manager.filter(Q(username__iexact=username) | Q(email__iexact=username))
        candidates = sorted(users, key=lambda u: (u.username.lower() != username.lower(), not u.is_active))
        for user in candidates:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        # Run the hasher once anyway, so timing doesn't reveal whether the account exists.
        get_user_model()().set_password(password)
        return None

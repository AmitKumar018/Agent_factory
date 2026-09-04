from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db.models import Q


class Command(BaseCommand):

    help = "Creates a superuser with given arguments."

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, help="Admin Username")
        parser.add_argument('--email', type=str, help="Admin email")
        parser.add_argument('--password', type=str, help="Admin password")

    def handle(self, *args, **kwargs):
        username = kwargs.get("username")
        email = kwargs.get("email")
        password = kwargs.get("password")

        if not username or not email or not password:
            print("Username, Email and Password are required for superuser creation.")
            return

        User = get_user_model()

        existing_users_with_given_creds = User.objects.filter(
            Q(username=username) | Q(email=email)
        ).count()
        if existing_users_with_given_creds > 0:
            print("Given email or username already exists.")
        else:
            User.objects.create_superuser(
                username=username, email=email, password=password
            )
            print("Superuser created.")

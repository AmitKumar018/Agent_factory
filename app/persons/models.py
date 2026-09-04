from statistics import mode
from django.db import models


class Person(models.Model):
    name = models.CharField(max_length=32)
    age = models.IntegerField()

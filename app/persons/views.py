from rest_framework.views import APIView
from rest_framework.response import Response

from .models import Person
from .serializers import PersonSerializer


class PersonView(APIView):
    def get(self, request):
        persons = Person.objects.all()
        response = PersonSerializer(persons, many=True)
        return Response({"data": response.data})

    def post(self, request):
        data = request.data
        serializer = PersonSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=201)

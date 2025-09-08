from django.contrib import admin
from .models import Product, UserProfile, Business, Order, Return, SalesForecastModel

# Register your models here.
admin.site.register(Product)
admin.site.register(UserProfile)
admin.site.register(Business)
admin.site.register(Order)
admin.site.register(Return)
admin.site.register(SalesForecastModel)

class ProductAdmin(admin.ModelAdmin):
    exclude = ('business',)  # hide business field
from django.conf import settings
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.core.serializers.json import DjangoJSONEncoder

# -------------------------
# Custom User Model
# -------------------------
class UserProfile(AbstractUser):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('staff', 'Staff'),
    ]

    full_name = models.CharField(max_length=255)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    def __str__(self):
        return self.username


# -------------------------
# Business Model
# -------------------------
class Business(models.Model):
    DEPARTMENT_CHOICES = [
        ('sales', 'Sales'),
        ('marketing', 'Marketing'),
        ('hr', 'Human Resources'),
        ('it', 'IT Department'),
        ('finance', 'Finance'),
    ]

    owner = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="businesses")
    business_name = models.CharField(max_length=255, blank=True, null=True)
    business_type = models.CharField(max_length=100, blank=True, null=True)
    contact_number = models.CharField(max_length=20, blank=True, null=True)
    gst_tax_id = models.CharField(max_length=100, blank=True, null=True)
    business_address = models.TextField(blank=True, null=True)
    department_branch = models.CharField(max_length=50, choices=DEPARTMENT_CHOICES, blank=True, null=True)

    def __str__(self):
        return self.business_name if self.business_name else f"Business of {self.owner.username}"


# -------------------------
# Product Model
# -------------------------
class Product(models.Model):
    CATEGORY_CHOICES = [
        ('electronics', 'Electronics'),
        ('furniture', 'Furniture'),
        ('apparel', 'Apparel'),
        ('books', 'Books'),
        ('kitchen', 'Kitchen'),
        ('gaming', 'Gaming'),
        ('beauty', 'Beauty'),
        ('office', 'Office'),
        ('sports', 'Sports'),
        ('toys', 'Toys'),
        ('groceries', 'Groceries / Food & Beverages'),
        ('automotive', 'Automotive / Vehicle Accessories'),
        ('health', 'Health / Personal Care'),
        ('stationery', 'Stationery / School Supplies'),
        ('home_decor', 'Home Decor / Garden'),
    ]

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="products",
        null=True,
        blank=True
    )
    product_name = models.CharField(max_length=255)
    sku = models.CharField(max_length=100)  # ❌ remove unique=True
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    current_stock = models.IntegerField(default=0)
    min_stock = models.IntegerField(default=0)
    max_stock = models.IntegerField(default=0)
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    supplier = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("business", "sku")  # ✅ SKU unique only per business

    def save(self, *args, **kwargs):
        if not self.business_id:
            self.business_id = 1  # default business ID (optional, but fine)
        super().save(*args, **kwargs)

class Order(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="orders", blank=True, null=True)
    order_id = models.CharField(max_length=100)
    tracking_id = models.CharField(max_length=100, blank=True, null=True)
    product_name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    customer_name = models.CharField(max_length=255)
    
    # 🟢 Remove auto_now_add=True
    date = models.DateField()
    
    is_returned = models.BooleanField(default=False)

    def __str__(self):
        return f"Order {self.order_id} - {self.product_name}"
    

# your_app/models.py

from django.db import models

class Return(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="returns", blank=True, null=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="returns")
    product_name = models.CharField(max_length=255)
    customer_name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    
    # 🟢 Remove auto_now_add=True
    date = models.DateField()

    def __str__(self):
        return f"Return for {self.product_name} ({self.quantity})"
    

class SalesForecastModel(models.Model):
    """
    Stores the trained sales forecast model parameters for a business.
    """
    business = models.OneToOneField(Business, on_delete=models.CASCADE)
    
    # Store the coefficients as a JSON field to handle arrays
    coefficients = models.JSONField(encoder=DjangoJSONEncoder, default=list)
    intercept = models.FloatField(default=0.0)
    polynomial_degree = models.IntegerField(default=2)

    def __str__(self):
        return f"Sales Forecast Model for {self.business.name}"

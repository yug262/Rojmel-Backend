from rest_framework import serializers
from .models import UserProfile, Business, Product, Order, Return
import re

class UserProfileSerializer(serializers.ModelSerializer):
    confirm_password = serializers.CharField(write_only=True, min_length=6)
    business_name = serializers.CharField(required=False, allow_blank=True)
    business_type = serializers.CharField(required=False, allow_blank=True)
    contact_number = serializers.CharField(required=False, allow_blank=True)
    gst_tax_id = serializers.CharField(required=False, allow_blank=True)
    business_address = serializers.CharField(required=False, allow_blank=True)
    department_branch = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = UserProfile
        fields = [
            "full_name", "username", "email", "role",
            "password", "confirm_password",
            "business_name", "business_type", "contact_number",
            "gst_tax_id", "business_address", "department_branch",
        ]
        extra_kwargs = {
            "password": {"write_only": True, "min_length": 6},
            "email": {"required": False, "allow_blank": True},
        }

    def validate(self, data):
        errors = {}

        if data.get("username"):
            data["username"] = data["username"].strip()
        if data.get("email"):
            data["email"] = data["email"].strip().lower()
        if data.get("contact_number"):
            data["contact_number"] = data["contact_number"].strip()

        if not data.get("full_name"):
            errors["full_name"] = ["Full name is required."]
        if not data.get("username"):
            errors["username"] = ["Username is required."]
        if not data.get("role"):
            errors["role"] = ["Role is required."]
        if not data.get("password"):
            errors["password"] = ["Password is required."]
        if not data.get("confirm_password"):
            errors["confirm_password"] = ["Confirm password is required."]

        if data.get("password") and data.get("confirm_password"):
            if data["password"] != data["confirm_password"]:
                errors["confirm_password"] = ["Passwords do not match."]
            if not re.match(r"^(?=.*[A-Z])(?=.*\d).{6,}$", data["password"]):
                errors["password"] = ["Password must have at least 6 chars, 1 uppercase, 1 number."]

        if data.get("username") and UserProfile.objects.filter(username__iexact=data["username"]).exists():
            errors["username"] = ["Username already exists."]
        if data.get("email"):
            try:
                serializers.EmailField().run_validation(data["email"])
            except serializers.ValidationError:
                errors["email"] = ["Enter a valid email."]
            else:
                if UserProfile.objects.filter(email__iexact=data["email"]).exists():
                    errors["email"] = ["Email already exists."]

        if data.get("contact_number"):
            if not re.match(r"^\+?\d{10,15}$", data["contact_number"]):
                errors["contact_number"] = ["Enter a valid contact number (10–15 digits, optional +)."]

        if errors:
            raise serializers.ValidationError(errors)
        return data

    def create(self, validated_data):
        password = validated_data.pop("password")
        validated_data.pop("confirm_password", None)

        business_payload = {
            "business_name": validated_data.pop("business_name", "").strip(),
            "business_type": validated_data.pop("business_type", "").strip(),
            "contact_number": validated_data.pop("contact_number", "").strip(),
            "gst_tax_id": validated_data.pop("gst_tax_id", "").strip(),
            "business_address": validated_data.pop("business_address", "").strip(),
            "department_branch": validated_data.pop("department_branch", "").strip(),
        }

        user = UserProfile.objects.create(**validated_data)
        user.set_password(password)
        user.save()

        if any(business_payload.values()):
            Business.objects.create(owner=user, **business_payload)

        return user

class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = "__all__"

    def validate(self, data):
        required_fields = ["product_name", "sku", "price", "selling_price"]
        errors = {}
        for field in required_fields:
            if not data.get(field):
                errors[field] = f"{field.replace('_', ' ').title()} is required."
        if errors:
            raise serializers.ValidationError(errors)
        return data
    
    def validate_sku(self, value):
        # 🟢 CORRECTED: Logic to handle edits
        request = self.context.get("request")
        context_business = self.context.get("business_for_validation")
        if request and request.user.is_authenticated:
            business = context_business or request.user.businesses.first()
            if business:
                # 🟢 Exclude the current instance if it exists (for edits)
                query = Product.objects.filter(business=business, sku=value)
                if self.instance:
                    query = query.exclude(pk=self.instance.pk)
                
                if query.exists():
                    raise serializers.ValidationError("SKU already exists for your business!")
        return value

class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = ['id', 'order_id', 'tracking_id', 'product_name', 'quantity', 'customer_name', 'date', 'is_returned']
        read_only_fields = ['business', 'is_returned']

    def create(self, validated_data):
        request = self.context.get("request")
        context_business = self.context.get("business_for_create")
        if request and request.user.is_authenticated:
            business = context_business or request.user.businesses.first()
            if not business:
                raise serializers.ValidationError({"business": "No business found for this user."})
            validated_data['business'] = business
        
        # 🟢 The date is now part of validated_data, so it will be saved.
        return super().create(validated_data)

# In serializers.py

class ReturnSerializer(serializers.ModelSerializer):
    # Make optional to allow passing the resolved order via context
    order = serializers.PrimaryKeyRelatedField(queryset=Order.objects.all(), required=False)

    order_id = serializers.CharField(source='order.order_id', read_only=True)
    tracking_id = serializers.CharField(source='order.tracking_id', read_only=True, allow_null=True)

    class Meta:
        model = Return
        # 🟢 Correct: Remove 'user' from the fields list
        fields = ['id', 'order', 'order_id', 'tracking_id', 'quantity', 'date', 'product_name', 'customer_name']
        # 🟢 Correct: Remove 'user' from read_only_fields
        read_only_fields = ['product_name', 'customer_name', 'business']

    def validate(self, attrs):
        provided_order = self.context.get("provided_order")
        if provided_order and "order" not in attrs:
            attrs["order"] = provided_order
        return super().validate(attrs)

    def create(self, validated_data):
        request = self.context.get("request")
        order = validated_data['order']

        # Prefer business from context (provided by view), else from order, else user's first
        context_business = self.context.get("business_for_create")
        resolved_business = context_business or getattr(order, 'business', None)

        if request and request.user.is_authenticated:
            if not resolved_business:
                user_first_business = request.user.businesses.first()
                if not user_first_business:
                    raise serializers.ValidationError({"business": "No business found for this user."})
                resolved_business = user_first_business

            # If the order has a business set, enforce it matches the resolved business
            if getattr(order, 'business', None) and order.business != resolved_business:
                raise serializers.ValidationError({"order": "Order does not belong to your business."})

            validated_data['business'] = resolved_business

        validated_data['product_name'] = order.product_name
        validated_data['customer_name'] = order.customer_name

        return super().create(validated_data)
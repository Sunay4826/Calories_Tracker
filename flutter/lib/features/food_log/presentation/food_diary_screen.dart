import 'package:flutter/material.dart';
import 'package:calories_tracker/core/theme/app_colors.dart';
import 'package:calories_tracker/core/theme/app_spacing.dart';
import 'package:calories_tracker/features/auth/data/auth_session_service.dart';
import 'package:calories_tracker/features/food_log/data/food_log_api_client.dart';

/// The user's saved meals for the last 30 days (the backend keeps no more) with daily totals.
class FoodDiaryScreen extends StatefulWidget {
  const FoodDiaryScreen({super.key});

  @override
  State<FoodDiaryScreen> createState() => _FoodDiaryScreenState();
}

class _FoodDiaryScreenState extends State<FoodDiaryScreen> {
  final FoodLogApiClient _api = FoodLogApiClient();
  final AuthSessionService _authSessionService = AuthSessionService();
  List<FoodDiaryDay> _days = const [];
  bool _loading = true;
  String? _error;
  String? _phone;
  String? _token;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    _phone ??= await _authSessionService.getSessionPhoneNumber();
    _token ??= await _authSessionService.getSessionAccessToken();
    if (_phone == null || _token == null) {
      setState(() {
        _loading = false;
        _error = 'Session expired. Please login again.';
      });
      return;
    }
    try {
      final days = await _api.history(phoneNumber: _phone!, accessToken: _token!, days: 30);
      if (mounted) setState(() => _days = days);
    } on FoodLogApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _delete(FoodMeal meal) async {
    try {
      await _api.deleteMeal(phoneNumber: _phone!, accessToken: _token!, mealId: meal.id);
      await _load();
    } on FoodLogApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  String _n(double? v) => (v ?? 0).toStringAsFixed(0);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: AppColors.background,
        foregroundColor: AppColors.textPrimary,
        title: const Text('Food diary'),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(AppSpacing.md),
                children: [
                  if (_error != null) Text(_error!, style: const TextStyle(color: AppColors.warning)),
                  if (_days.isEmpty && _error == null)
                    const Text('No meals logged in the last 30 days.',
                        style: TextStyle(color: AppColors.textSecondary)),
                  ..._days.map(
                    (day) => Card(
                      color: AppColors.surface,
                      child: Padding(
                        padding: const EdgeInsets.all(AppSpacing.sm),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              // The six numbers a saved meal keeps (DIARY_NUTRIENTS on the backend).
                              '${day.date} · ${_n(day.totals['energy_kcal'])} kcal · '
                              'P ${_n(day.totals['protein_g'])} g · F ${_n(day.totals['fat_g'])} g · '
                              'Fibre ${_n(day.totals['fibre_g'])} g · Sugar ${_n(day.totals['freesugar_g'])} g · '
                              'Sodium ${_n(day.totals['sodium_mg'])} mg',
                              style: const TextStyle(color: AppColors.textPrimary, fontWeight: FontWeight.w700),
                            ),
                            ...day.meals.map(
                              (meal) => ListTile(
                                dense: true,
                                contentPadding: EdgeInsets.zero,
                                title: Text(
                                  '${meal.mealType} · ${_n(meal.totals['energy_kcal'])} kcal',
                                  style: const TextStyle(color: AppColors.textPrimary),
                                ),
                                subtitle: Text(
                                  meal.itemNames.join(', '),
                                  style: const TextStyle(color: AppColors.textSecondary),
                                ),
                                trailing: IconButton(
                                  icon: const Icon(Icons.delete_outline, color: AppColors.textSecondary),
                                  onPressed: () => _delete(meal),
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
    );
  }
}

package SRP;

public class CardioVascularSymptom extends Symptom {

	public CardioVascularSymptom(int covidImpact, int severityIndex, String name, Double affectedDays) {
		super(covidImpact, severityIndex, name, affectedDays);
		// TODO Auto-generated constructor stub
	}

	@Override
	public double getProbabilityCovid19() {
		// TODO Auto-generated method stub
		return 60;
	}

	@Override
	protected double getSeverityIndex() {
		// TODO Auto-generated method stub
		return severityIndex;
	}

	public double geaffectedDays() {
		return affectedDays;
	}

	@Override
	protected int getCovidImpact() {
		// TODO Auto-generated method stub
		return covidImpact;
	}


}
